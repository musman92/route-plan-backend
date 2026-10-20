from rest_framework import serializers

SAMPLE_MILES = (10, 25, 50)


class PlanInputSerializer(serializers.Serializer):
    origin = serializers.CharField(max_length=200)
    destination = serializers.CharField(max_length=200)
    departure = serializers.DateTimeField()
    load_lbs = serializers.FloatField(min_value=0)
    sample_miles = serializers.IntegerField()

    def validate_origin(self, value: str) -> str:
        return _required_place(value, "Enter an origin.")

    def validate_destination(self, value: str) -> str:
        return _required_place(value, "Enter a destination.")

    def validate_sample_miles(self, value: int) -> int:
        if value not in SAMPLE_MILES:
            raise serializers.ValidationError("Sample interval must be 10, 25, or 50 miles.")
        return value


def _required_place(value: str, message: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise serializers.ValidationError(message)
    return cleaned
