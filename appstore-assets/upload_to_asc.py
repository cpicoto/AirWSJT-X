#!/usr/bin/env python3
"""Upload App Store metadata and screenshots to App Store Connect."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import jwt
import requests

CONFIG_PATH = os.path.expanduser("~/.openclaw/workspace/private/appstore-connect/config.json")
BUNDLE_ID = "us.ad7np.airwsjt-x"
METADATA_PATH = Path(__file__).parent / "metadata.json"
REVIEW_NOTES_PATH = Path(__file__).parent / "review-notes.txt"
SCREENSHOTS_ROOT = Path(__file__).parent / "screenshots"
SCREENSHOTS_DIR = SCREENSHOTS_ROOT / "mac"

def load_config():
    """API key settings from ASC_KEY_ID / ASC_ISSUER_ID / ASC_KEY_PATH, else the config file."""
    if os.environ.get("ASC_KEY_ID") and os.environ.get("ASC_ISSUER_ID") and os.environ.get("ASC_KEY_PATH"):
        return {"key_id": os.environ["ASC_KEY_ID"], "issuer_id": os.environ["ASC_ISSUER_ID"],
                "key_path": os.environ["ASC_KEY_PATH"]}
    return json.loads(Path(CONFIG_PATH).read_text())

def get_token():
    config = load_config()
    key = Path(config["key_path"]).read_text()
    now = int(time.time())
    payload = {"iss": config["issuer_id"], "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}
    return jwt.encode(payload, key, algorithm="ES256", headers={"kid": config["key_id"]})

BASE = "https://api.appstoreconnect.apple.com/v1"

def headers():
    return {"Authorization": f"Bearer {get_token()}", "Content-Type": "application/json"}

def get(path):
    r = requests.get(f"{BASE}{path}", headers=headers())
    r.raise_for_status()
    return r.json()

def patch(path, data, raise_on_error=True):
    r = requests.patch(f"{BASE}{path}", headers=headers(), json=data)
    if not r.ok:
        print(f"PATCH {path} failed: {r.status_code} {r.text[:300]}")
        if raise_on_error:
            r.raise_for_status()
        return None
    return r.json()

def post(path, data):
    r = requests.post(f"{BASE}{path}", headers=headers(), json=data)
    if not r.ok:
        print(f"POST {path} failed: {r.status_code} {r.text}")
    r.raise_for_status()
    return r.json()

def delete(path):
    r = requests.delete(f"{BASE}{path}", headers=headers())
    if not r.ok:
        print(f"DELETE {path} failed: {r.status_code}")
    return r

def find_app():
    data = get(f"/apps?filter[bundleId]={BUNDLE_ID}")
    apps = data["data"]
    if not apps:
        sys.exit(f"No app found with bundle ID {BUNDLE_ID}")
    app = apps[0]
    print(f"Found app: {app['attributes']['name']} ({app['id']})")
    return app["id"]

PLATFORMS = {
    "mac": {
        "asc_platform": "MAC_OS",
        "dir": "mac",
        "screenshots": {
            "APP_DESKTOP": ["01-active-contact-2880x1800.png", "02-qso-in-progress-2880x1800.png"],
        },
    },
    "ios": {
        "asc_platform": "IOS",
        "dir": "iphone",
        "screenshots": {
            "APP_IPHONE_67": ["iphone/01-tracking-1320x2868.png", "iphone/02-tracking-1320x2868.png"],
            "APP_IPAD_PRO_3GEN_129": ["ipad/01-tracking-2048x2732.png", "ipad/02-tracking-2048x2732.png"],
        },
    },
}

def get_latest_version(app_id, asc_platform="MAC_OS"):
    data = get(f"/apps/{app_id}/appStoreVersions?limit=10")
    versions = data["data"]
    matching = [v for v in versions if v["attributes"]["platform"] == asc_platform]
    if not matching:
        print(f"No {asc_platform} App Store versions found.")
        return None
    v = matching[0]
    print(f"Latest version: {v['attributes']['versionString']} (state: {v['attributes']['appStoreState']}, id: {v['id']})")
    return v

def get_or_create_localization(version_id, locale="en-US"):
    data = get(f"/appStoreVersions/{version_id}/appStoreVersionLocalizations")
    for loc in data["data"]:
        if loc["attributes"]["locale"] == locale:
            print(f"Found existing {locale} localization: {loc['id']}")
            return loc["id"]
    result = post("/appStoreVersionLocalizations", {
        "data": {
            "type": "appStoreVersionLocalizations",
            "attributes": {"locale": locale},
            "relationships": {
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}}
            }
        }
    })
    loc_id = result["data"]["id"]
    print(f"Created {locale} localization: {loc_id}")
    return loc_id

def update_localization(loc_id, metadata, ios=False):
    en = metadata.get("en-US", {})
    attrs = {}
    if en.get("description"):
        attrs["description"] = en["description"]
        if ios:
            attrs["description"] = attrs["description"].replace("• macOS 12.0 or later\n", "")
    if en.get("keywords"):
        attrs["keywords"] = en["keywords"]
    if en.get("promotional_text"):
        attrs["promotionalText"] = en["promotional_text"]
    if metadata.get("support_url"):
        attrs["supportUrl"] = metadata["support_url"]
    if metadata.get("marketing_url"):
        attrs["marketingUrl"] = metadata["marketing_url"]
    if not attrs:
        print("No metadata to update")
        return
    result = patch(f"/appStoreVersionLocalizations/{loc_id}", {
        "data": {"type": "appStoreVersionLocalizations", "id": loc_id, "attributes": attrs}
    }, raise_on_error=False)
    if result:
        print(f"Updated localization metadata: {list(attrs.keys())}")
    return result

def update_app_info(app_id, metadata):
    data = get(f"/apps/{app_id}/appInfos")
    if not data["data"]:
        print("No app info found")
        return
    info = data["data"][0]
    info_id = info["id"]

    info_locs = get(f"/appInfos/{info_id}/appInfoLocalizations")
    en_loc = None
    for loc in info_locs["data"]:
        if loc["attributes"]["locale"] == "en-US":
            en_loc = loc
            break

    if en_loc:
        attrs = {}
        en = metadata.get("en-US", {})
        if en.get("subtitle"):
            attrs["subtitle"] = en["subtitle"]
        if en.get("name"):
            attrs["name"] = en["name"]
        if attrs:
            result = patch(f"/appInfoLocalizations/{en_loc['id']}", {
                "data": {"type": "appInfoLocalizations", "id": en_loc["id"], "attributes": attrs}
            }, raise_on_error=False)
            if result:
                print(f"Updated app info: {list(attrs.keys())}")

    if metadata.get("privacy_policy_url"):
        try:
            patch(f"/appInfos/{info_id}", {
                "data": {
                    "type": "appInfos", "id": info_id,
                    "attributes": {}
                }
            })
        except Exception:
            pass


def update_review_notes(version_id):
    """Publish the checked-in App Review notes for the macOS version."""
    notes = REVIEW_NOTES_PATH.read_text(encoding="utf-8").strip()
    if not notes:
        raise ValueError(f"App Review notes are empty: {REVIEW_NOTES_PATH}")

    detail = get(f"/appStoreVersions/{version_id}/appStoreReviewDetail")["data"]
    detail_id = detail["id"]
    patch(f"/appStoreReviewDetails/{detail_id}", {
        "data": {
            "type": "appStoreReviewDetails",
            "id": detail_id,
            "attributes": {"notes": notes},
        }
    })
    print("Updated App Review notes")


def upload_screenshot(loc_id, filepath, display_type, position):
    filepath = Path(filepath)
    file_size = filepath.stat().st_size
    file_name = filepath.name

    with open(filepath, "rb") as f:
        checksum = hashlib.md5(f.read()).hexdigest()

    reservation = post("/appScreenshots", {
        "data": {
            "type": "appScreenshots",
            "attributes": {
                "fileName": file_name,
                "fileSize": file_size
            },
            "relationships": {
                "appScreenshotSet": {
                    "data": {"type": "appScreenshotSets", "id": loc_id}
                }
            }
        }
    })

    screenshot_id = reservation["data"]["id"]
    upload_ops = reservation["data"]["attributes"].get("uploadOperations", [])
    print(f"  Reserved screenshot {screenshot_id}, {len(upload_ops)} upload operations")

    with open(filepath, "rb") as f:
        file_data = f.read()

    for op in upload_ops:
        offset = op["offset"]
        length = op["length"]
        chunk = file_data[offset:offset + length]
        upload_headers = {h["name"]: h["value"] for h in op["requestHeaders"]}
        r = requests.put(op["url"], headers=upload_headers, data=chunk)
        if not r.ok:
            print(f"  Upload chunk failed: {r.status_code}")
            return None

    patch(f"/appScreenshots/{screenshot_id}", {
        "data": {
            "type": "appScreenshots", "id": screenshot_id,
            "attributes": {"uploaded": True, "sourceFileChecksum": checksum}
        }
    })
    print(f"  Uploaded and committed: {file_name}")
    return screenshot_id

def get_or_create_screenshot_set(loc_id, display_type):
    data = get(f"/appStoreVersionLocalizations/{loc_id}/appScreenshotSets")
    for ss_set in data["data"]:
        if ss_set["attributes"]["screenshotDisplayType"] == display_type:
            print(f"Found existing screenshot set for {display_type}: {ss_set['id']}")
            return ss_set["id"]
    result = post("/appScreenshotSets", {
        "data": {
            "type": "appScreenshotSets",
            "attributes": {"screenshotDisplayType": display_type},
            "relationships": {
                "appStoreVersionLocalization": {
                    "data": {"type": "appStoreVersionLocalizations", "id": loc_id}
                }
            }
        }
    })
    set_id = result["data"]["id"]
    print(f"Created screenshot set for {display_type}: {set_id}")
    return set_id

def clear_existing_screenshots(set_id):
    data = get(f"/appScreenshotSets/{set_id}/appScreenshots")
    for ss in data["data"]:
        print(f"  Deleting existing screenshot: {ss['id']}")
        delete(f"/appScreenshots/{ss['id']}")

def main():
    platform = PLATFORMS[sys.argv[1] if len(sys.argv) > 1 else "mac"]
    metadata = json.loads(METADATA_PATH.read_text())

    print("=== Finding app ===")
    app_id = find_app()

    print("\n=== Getting latest App Store version ===")
    version = get_latest_version(app_id, platform["asc_platform"])
    if not version:
        print("No version to update. Create a version in App Store Connect first.")
        return

    version_id = version["id"]

    if platform is PLATFORMS["mac"]:
        print("\n=== Updating App Review notes ===")
        update_review_notes(version_id)

    print("\n=== Updating localization metadata ===")
    loc_id = get_or_create_localization(version_id, "en-US")
    update_localization(loc_id, metadata, ios=platform is PLATFORMS["ios"])

    print("\n=== Updating app info ===")
    update_app_info(app_id, metadata)

    print("\n=== Uploading screenshots ===")
    screenshot_map = {t: [(f, n) for n, f in enumerate(files)] for t, files in platform["screenshots"].items()}

    for display_type, screenshots in screenshot_map.items():
        set_id = get_or_create_screenshot_set(loc_id, display_type)
        clear_existing_screenshots(set_id)
        for filename, position in screenshots:
            filepath = (SCREENSHOTS_ROOT / filename) if "/" in filename else (SCREENSHOTS_DIR / filename)
            if filepath.exists():
                upload_screenshot(set_id, filepath, display_type, position)
            else:
                print(f"  WARNING: {filepath} not found, skipping")

    print("\n=== Done ===")
    print("Next steps in App Store Connect:")
    print("1. Review the uploaded metadata and screenshots")
    print("2. Set the privacy policy URL")
    print("3. Complete age rating questionnaire")
    print("4. Submit for review")

if __name__ == "__main__":
    main()
