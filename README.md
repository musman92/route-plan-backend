# Weather-Aware Truck Routing — Backend

Django + Django REST Framework API that plans a truck trip: it geocodes the origin and destination, builds up to three route options, checks the forecast at each checkpoint for the time the truck is expected to reach it, scores the weather risk using the provided wind/rain/snow and load rules, and recommends a route.

The frontend lives in a separate repository (`weather-aware` frontend). It calls this API at `POST /api/plan/`.

## Tech stack

- Python 3.11, Django 5, Django REST Framework
- `django-cors-headers` for the frontend origin
- `requests` for third-party APIs
- No database models; the SQLite setting exists only because Django requires a database config

## Third-party services (no API keys required)

- Geocoding: [Nominatim / OpenStreetMap](https://nominatim.org/)
- Routing: [OSRM public demo server](https://project-osrm.org/)
- Weather: [Open-Meteo hourly forecast](https://open-meteo.com/) (`wind_speed_10m`, `rain`, `snowfall`; 7 days)

## Getting started

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py runserver 8000
```

The API is now available at `http://127.0.0.1:8000/api/plan/`.

### Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DEBUG` | `True` | Set to `False` in production |
| `SECRET_KEY` | dev-only value | Set a long random value in production |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames served by Django |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated frontend origins |

## API

### `POST /api/plan/`

Request:

```json
{
  "origin": "Chicago, IL",
  "destination": "New York, NY",
  "departure": "2026-10-23T13:00:00Z",
  "load_lbs": 40000,
  "sample_miles": 25
}
```

`sample_miles` must be `10`, `25`, or `50`. `load_lbs` must be zero or more.

Response (shortened):

```json
{
  "origin": { "label": "Chicago, ...", "lat": 41.88, "lon": -87.63 },
  "destination": { "label": "New York, ...", "lat": 40.71, "lon": -74.0 },
  "departure": "2026-10-23T13:00:00Z",
  "load_lbs": 40000,
  "sample_miles": 25,
  "recommendation": {
    "route_id": "route-1",
    "reason": "It has the fewest severe miles."
  },
  "routes": [
    {
      "id": "route-1",
      "name": "Route 1",
      "recommended": true,
      "distance_miles": 790.2,
      "duration_minutes": 760.4,
      "no_travel_miles": 0,
      "severe_miles": 0,
      "high_miles": 12.5,
      "moderate_miles": 50,
      "low_miles": 727.7,
      "unknown_miles": 0,
      "average_risk": 1.09,
      "geometry": { "type": "LineString", "coordinates": [[-87.63, 41.88]] },
      "checkpoints": [
        {
          "lat": 41.88, "lon": -87.63, "mile": 0, "eta": "2026-10-23T13:00:00Z",
          "wind_mph": 12.4, "rain_in_hr": 0, "snow_in_hr": 0,
          "wind_risk": "low", "rain_risk": "low", "snow_risk": "low", "risk": "low"
        }
      ]
    }
  ],
  "heatmap": {
    "times": ["2026-10-22T20:00:00Z"],
    "points": [{ "lat": 41.88, "lon": -87.63, "risks": [1, 1, 2] }]
  }
}
```

Errors return `{"error": "..."}` with `400` (invalid input or same place), `404` (location or route not found), or `502` (a third-party service failed). Field validation errors return DRF's field-to-messages object.

## How the planning works

1. **Geocode** origin and destination with Nominatim, waiting so requests stay at most one per second.
2. **Routes** — request OSRM with alternatives. Near-duplicate routes (90% or more overlap) are dropped. If fewer than three remain, the planner asks OSRM for routes through points offset to either side of the main route and keeps candidates that are no more than 45% longer and clearly different. OSRM can still return fewer than three routes for short or constrained trips.
3. **Checkpoints** — each route is sampled every 10, 25, or 50 miles, always including the start and the end. Each checkpoint's ETA is departure time plus the route's travel time to that point.
4. **Weather** — forecasts for all checkpoints are fetched from Open-Meteo in batches of 50 locations. A checkpoint uses the forecast hour closest to its ETA, within 90 minutes; outside that window it is marked unavailable rather than assumed safe.
5. **Risk** — each checkpoint gets a wind, rain, and snow level, and its overall risk is the worst of the three.
6. **Route score** — each checkpoint represents the road halfway to its neighbors, so risk is counted in miles. Average risk is the mile-weighted average of levels 1–5 over miles with known weather.
7. **Recommendation** — routes are ranked, and the response names the winning route plus the first rule that decided it (`recommendation.reason`).
8. **Heatmap** — the corridor of every route is sampled every 20 miles and scored for each of the next 49 forecast hours (0–48 h), so the frontend slider needs no extra requests.

Geocoding, route results, and hourly forecasts are cached in memory for 15 minutes, so repeating a trip during a demo does not call the outside services again. Nominatim is still limited to one request per second on a cache miss.

### Risk rules

| Condition | Low | Moderate | High | Severe | No Travel |
| --- | --- | --- | --- | --- | --- |
| Wind (mph) | < 25 | 25–34 | 35–44 | 45–54 | ≥ 55 |
| Rain (in/hr) | < 0.10 | 0.10–0.25 | 0.25–0.50 | 0.50–1.00 | > 1.00 |
| Snow (in/hr) | < 0.5 | 0.5–1.0 | 1.0–2.0 | 2.0–3.0 | > 3.0 |

Load rules applied to wind:

- 55 mph or more is No Travel for any load.
- 45–54 mph with a load over 30,000 lb is No Travel.
- 35–44 mph with a load over 40,000 lb is Severe.

Where the bands share an edge (for example rain at exactly 0.25 in/hr), the value goes to the higher band. Rain of exactly 1.00 in/hr and snow of exactly 3.0 in/hr are Severe, because No Travel is defined as greater than those values.

### Recommendation order

Routes are sorted by:

1. Routes with no usable forecast go last, so missing data is never treated as safe.
2. Fewest No Travel miles, because a blocked road is worse than a Severe one.
3. Fewest Severe miles
4. Fewest High miles
5. Lowest average risk
6. Shortest travel time

If no route has forecast coverage (for example, a departure more than seven days ahead), no route is recommended.

## Project layout

```
backend/
  config/            Django settings and root URLs
  apps/
    planner/         API view, input serializer, trip planning orchestration
    routing/         Nominatim geocoding, OSRM routes, track geometry and sampling
    weather/         Open-Meteo client and hourly forecast lookup
    risk/            Risk rules, mile-weighted scoring, recommendation key
```

## Tests

```bash
source .venv/bin/activate
python manage.py test
```

Tests cover the risk band edges and load rules, mile ownership and scoring, the recommendation order, missing forecasts, track building and sampling, forecast hour lookup and unit conversion, and API input validation. They do not call external services.

## Deployment (VPS)

A typical setup runs Django with Gunicorn behind Nginx:

```bash
pip install gunicorn
DEBUG=False SECRET_KEY=... ALLOWED_HOSTS=api.example.com \
CORS_ALLOWED_ORIGINS=https://app.example.com \
gunicorn config.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 120
```

Use a long Gunicorn timeout: one plan makes several calls to outside services and can take 10–30 seconds on long routes.

## Known limitations

- The public OSRM and Nominatim servers are rate-limited and best-effort; heavy use needs self-hosted or paid providers.
- OSRM's car profile is used. It does not know about truck restrictions such as height, weight, or hazmat limits.
- Open-Meteo forecasts cover seven days. Checkpoints with ETAs past that are shown as unavailable.
