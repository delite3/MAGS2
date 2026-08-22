import json
import urllib.error
import urllib.parse
import urllib.request


BASE_URL = "http://172.27.240.1:30010"
PRESET_NAME = "RCP_SimControl"


def property_url(property_name):
    preset = urllib.parse.quote(PRESET_NAME, safe="")
    prop = urllib.parse.quote(property_name, safe="")

    return f"{BASE_URL}/remote/preset/{preset}/property/{prop}"


def get_property(property_name):
    request = urllib.request.Request(
        property_url(property_name),
        method="GET",
        headers={"Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=2.0) as response:
        result = json.load(response)

    values = result.get("PropertyValues", [])

    if len(values) != 1:
        raise RuntimeError(
            f"Expected one value for {property_name}, got {len(values)}"
        )

    return values[0]["PropertyValue"]


def set_property(property_name, value):
    payload = json.dumps(
        {
            "PropertyValue": value,
            "GenerateTransaction": False,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        property_url(property_name),
        data=payload,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=2.0) as response:
        if response.status != 200:
            raise RuntimeError(f"UE returned HTTP {response.status}")


def main():
    original_location = get_property("VehicleLocation")
    original_rotation = get_property("VehicleRotation")

    print(f"Original location: {original_location}")
    print(f"Original rotation: {original_rotation}")

    moved_location = dict(original_location)
    moved_location["X"] += 200.0  # Move two metres along X.

    import time

    start = time.perf_counter()
    set_property("VehicleLocation", moved_location)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"PUT response: {elapsed_ms:.2f} ms")

    confirmed_location = get_property("VehicleLocation")
    print(f"Moved location:    {confirmed_location}")

    input("The cone should now be two metres away. Press Enter to restore it...")

    start = time.perf_counter()
    set_property("VehicleLocation", original_location)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"PUT response: {elapsed_ms:.2f} ms")
    print(f"Restored location: {get_property('VehicleLocation')}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        print(f"HTTP {error.code}: {response_body}")
        raise