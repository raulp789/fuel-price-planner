from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200,
        trim_whitespace=True,
        help_text='Start location in the USA: "City, ST", "lat,lon" or a full address.',
    )
    finish = serializers.CharField(
        max_length=200,
        trim_whitespace=True,
        help_text='Finish location in the USA: "City, ST", "lat,lon" or a full address.',
    )
