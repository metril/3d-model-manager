"""Recorded `design-service/design/{id}` and `search-service/select/design`
bodies for MakerWorld (Workstream B task B1). The free-model fixture trims
the REAL live response captured for design 3018898 (grounding probe) down to
the fields ``fetch_metadata``/``search`` actually read; the paid fixture is
hand-built (``paidSetting.isPaid`` was ``false`` on every one of 200
live-sampled designs during grounding, so a genuinely paid example wasn't
observed) with a minimal, structurally faithful ``paidSetting`` shape."""

DESIGN_ID = "3018898"

DESIGN_3018898 = {
    "id": 3018898,
    "title": "NASA Fabric: Pokeball (No AMS Needed)",
    "slug": "nasa-fabric-pokeball-no-ams-needed",
    "summary": "<p>Boost if you like my model!</p>",
    "designCreator": {
        "uid": 2225712507,
        "name": "MeasureOnce",
        "avatar": "https://public-cdn.bblmw.com/avatar/2225712507/2024-11-12_5bd28d.jpg",
    },
    "license": "Standard Digital File License",
    "coverUrl": "https://makerworld.bblmw.com/makerworld/model/US1efdb154b59848/design/746db.gif",
    "tags": ["nasa fabric", "ball", "pokeball", "fidget"],
    # Live-verified (grounding, 200-design sample): true on ~90% of ALL
    # designs regardless of price -- a creator-rewards program badge, not a
    # paywall. NOT importable-gating (see app/importers/makerworld.py).
    "isExclusive": True,
    "paidSetting": {"isPaid": False, "crowdfunding": 0},
    "isPrintable": True,
    "modelId": "US1efdb154b59848",
    "defaultInstanceId": 3391581,
    "instances": [
        {
            "id": 3391581,
            "title": "Default",
            "hasZipStl": True,
            "profileId": 12345,
            "downloadCount": 351,
            "pictures": [{"url": "https://makerworld.bblmw.com/pic1.jpg"}],
        }
    ],
}

# Raw-file-only design (trimmed from live design 541281): no print-profile
# instances, but the creator uploaded raw model files.
DESIGN_RAW_ONLY = {
    **DESIGN_3018898,
    "id": 541281,
    "title": "PCIe 92mm fan bracket v3",
    "modelId": "US179caeb8dbff35",
    "instances": [],
    "rawModelFileDownloadCount": 169,
}

# Live-captured shape of GET /design-service/design/541281/model
# ?modelType=all&type=download (2026-11-02).
RAW_MODEL_RESPONSE = {
    "name": "",
    "url": "https://makerworld.bblmw.com/makerworld/model/US179caeb8dbff35/mszipfile/1721011756/all.zip?sig=abc",
    "highCompression": "",
}

DESIGN_PAID = {
    **DESIGN_3018898,
    "id": 9999999,
    "title": "Paid Exclusive Design",
    "paidSetting": {"isPaid": True, "crowdfunding": 0},
}

SEARCH_QUERY = "benchy"

# LIVE-CAPTURED shape of MakerWorld's real keyword search: the Next.js SSR data
# route `/_next/data/<buildId>/en/search/models.json?keyword=<q>`, whose JSON is
# `{"pageProps": {"designs": [<item>...], "total": int, ...}, "__N_SSP": true}`.
# Each item: {id, title, slug, cover, designCreator: {uid, name, ...}, ...}.
# Reached ANONYMOUSLY -- keyword filtering was confirmed working against the
# live account (real "benchy" designs returned). The earlier
# `search-service/select/design` endpoint was a trending handler that ignored
# the keyword; see app/importers/makerworld.py's module docstring.
SEARCH_DESIGNS_BENCHY = [
    {
        "id": 3018898,
        "title": "NASA Fabric: Pokeball (No AMS Needed)",
        "slug": "nasa-fabric-pokeball-no-ams-needed",
        "cover": "https://makerworld.bblmw.com/makerworld/model/US1efdb154b59848/746db.gif",
        "designCreator": {"uid": 2225712507, "name": "MeasureOnce"},
        "likeCount": 664,
        "downloadCount": 351,
    },
    {
        "id": 3012887,
        "title": "12-in-1 Ultimate Multi Fidget Toy (Print in Place)",
        "slug": "12-in-1-ultimate-multi-fidget-toy",
        "cover": "https://makerworld.bblmw.com/cover2.gif",
        "designCreator": {"uid": 111, "name": "someone"},
        "likeCount": 10,
        "downloadCount": 5,
    },
]

# -- saved collections / likes (M9 A2) ------------------------------------

PROFILE_TERMINALFOO = {"uid": 3054026541, "name": "Terminalfoo"}

# Trimmed `pageProps.favoritesList[]` from the live-captured `collections.json`
# SSR route (mw_capture_notes.md) down to the fields `list_user_lists` reads:
# `id, title, slug, isDefault, designCnt, status`. Includes one `status: 2`
# (hidden) collection ("Trays") to exercise the skip-hidden filter.
FAVORITES_LIST = [
    {
        "id": 2155987,
        "title": "Default Collection",
        "slug": "default-collection",
        "isDefault": True,
        "designCnt": 7,
        "status": 1,
    },
    {
        "id": 18925823,
        "title": "ESP32",
        "slug": "esp32",
        "isDefault": False,
        "designCnt": 9,
        "status": 1,
    },
    {
        "id": 1793275,
        "title": "Trays",
        "slug": "trays",
        "isDefault": False,
        "designCnt": 3,
        "status": 2,
    },
]

# Trimmed real `/design-service/favorites/designs/{listId}` response
# (mw_items.json, aggregate list for @Terminalfoo) down to the fields
# `list_list_items` reads: `id, title, cover, designCreator{name}`.
FAVORITE_DESIGNS = [
    {
        "id": 2188414,
        "title": "ESP32-C6-Zigbee Gehäuse",
        "cover": "https://makerworld.bblmw.com/makerworld/model/DSM00000002188414/design/2026-01-02_e75176fea9bc4.jpg",
        "designCreator": {"uid": 3279776322, "name": "Jackstyle"},
    },
    {
        "id": 2603954,
        "title": "Housing for ESP32-C6 DevKitC",
        "cover": "https://makerworld.bblmw.com/makerworld/model/US340b8e8edf9b37/design/06dd9a086e50e095.jpeg",
        "designCreator": {"uid": 4268272778, "name": "Javier Lorenzana"},
    },
]

# Trimmed from live design 137041 (3 print profiles; plates cut to 3 each).
DESIGN_137041 = {
    "id": 137041,
    "title": "RJ45 Cap coupler Protection Cover for LAN cables",
    "modelId": "USd4c4619315b7fc",
    "coverUrl": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/design/2024-01-14_1cfc14dc5e9fd.jpg",
    "paidSetting": {"isPaid": False},
    "instances": [
        {
            "id": 151331,
            "title": "no brim, 0.2mm layer, 2 walls, 15% infill",
            "hasZipStl": True,
            "profileId": 166302083,
            "extention": {
                "modelInfo": {
                    "compatibility": {
                        "devModelName": "C12",
                        "devProductName": "P1S",
                        "nozzleDiameter": 0.4,
                    },
                    "otherCompatibility": [
                        {"devModelName": "C11", "devProductName": "P1P", "nozzleDiameter": 0.4},
                        {"devModelName": "BL-P002", "devProductName": "X1", "nozzleDiameter": 0.4},
                        {
                            "devModelName": "BL-P001",
                            "devProductName": "X1 Carbon",
                            "nozzleDiameter": 0.4,
                        },
                        {"devModelName": "C13", "devProductName": "X1E", "nozzleDiameter": 0.4},
                        {"devModelName": "N2S", "devProductName": "A1", "nozzleDiameter": 0.4},
                        {"devModelName": "N1", "devProductName": "A1 mini", "nozzleDiameter": 0.4},
                        {"devModelName": "O1D", "devProductName": "H2D", "nozzleDiameter": 0.4},
                        {"devModelName": "O1E", "devProductName": "H2D Pro", "nozzleDiameter": 0.4},
                        {"devModelName": "O1S", "devProductName": "H2S", "nozzleDiameter": 0.4},
                        {"devModelName": "N7", "devProductName": "P2S", "nozzleDiameter": 0.4},
                        {"devModelName": "O1C2", "devProductName": "H2C", "nozzleDiameter": 0.4},
                        {"devModelName": "N6", "devProductName": "X2D", "nozzleDiameter": 0.4},
                        {"devModelName": "N9", "devProductName": "A2L", "nozzleDiameter": 0.4},
                        {"devModelName": "N10", "devProductName": "H3C", "nozzleDiameter": 0.4},
                    ],
                    "plates": [
                        {
                            "index": 1,
                            "name": "labeled-1to24",
                            "thumbnail": {
                                "name": "plate_1.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/166302083/instance/plate_1.png",
                            },
                            "prediction": 8252,
                            "weight": 62,
                        },
                        {
                            "index": 2,
                            "name": "labeled-1to48",
                            "thumbnail": {
                                "name": "plate_2.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/166302083/instance/plate_2.png",
                            },
                            "prediction": 16243,
                            "weight": 124,
                        },
                        {
                            "index": 3,
                            "name": "unlabeled-cap",
                            "thumbnail": {
                                "name": "plate_3.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/166302083/instance/plate_3.png",
                            },
                            "prediction": 835,
                            "weight": 3,
                        },
                    ],
                }
            },
        },
        {
            "id": 151334,
            "title": "with brim, 0.2mm layer, 2 walls, 15% infill",
            "hasZipStl": True,
            "profileId": 113204515,
            "extention": {
                "modelInfo": {
                    "compatibility": {
                        "devModelName": "C12",
                        "devProductName": "P1S",
                        "nozzleDiameter": 0.4,
                    },
                    "otherCompatibility": [
                        {"devModelName": "C11", "devProductName": "P1P", "nozzleDiameter": 0.4},
                        {"devModelName": "BL-P002", "devProductName": "X1", "nozzleDiameter": 0.4},
                        {
                            "devModelName": "BL-P001",
                            "devProductName": "X1 Carbon",
                            "nozzleDiameter": 0.4,
                        },
                        {"devModelName": "C13", "devProductName": "X1E", "nozzleDiameter": 0.4},
                        {"devModelName": "N2S", "devProductName": "A1", "nozzleDiameter": 0.4},
                        {"devModelName": "N1", "devProductName": "A1 mini", "nozzleDiameter": 0.4},
                        {"devModelName": "O1D", "devProductName": "H2D", "nozzleDiameter": 0.4},
                        {"devModelName": "O1E", "devProductName": "H2D Pro", "nozzleDiameter": 0.4},
                        {"devModelName": "O1S", "devProductName": "H2S", "nozzleDiameter": 0.4},
                        {"devModelName": "N7", "devProductName": "P2S", "nozzleDiameter": 0.4},
                        {"devModelName": "O1C2", "devProductName": "H2C", "nozzleDiameter": 0.4},
                        {"devModelName": "N6", "devProductName": "X2D", "nozzleDiameter": 0.4},
                        {"devModelName": "N9", "devProductName": "A2L", "nozzleDiameter": 0.4},
                        {"devModelName": "N10", "devProductName": "H3C", "nozzleDiameter": 0.4},
                    ],
                    "plates": [
                        {
                            "index": 1,
                            "name": "labeled-1to24",
                            "thumbnail": {
                                "name": "plate_1.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/113204515/instance/plate_1.png",
                            },
                            "prediction": 8447,
                            "weight": 63,
                        },
                        {
                            "index": 2,
                            "name": "labeled-1to48",
                            "thumbnail": {
                                "name": "plate_2.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/113204515/instance/plate_2.png",
                            },
                            "prediction": 16560,
                            "weight": 125,
                        },
                        {
                            "index": 3,
                            "name": "unlabeled-cap",
                            "thumbnail": {
                                "name": "plate_3.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/113204515/instance/plate_3.png",
                            },
                            "prediction": 851,
                            "weight": 3,
                        },
                    ],
                }
            },
        },
        {
            "id": 1029382,
            "title": "Box for couplers",
            "hasZipStl": True,
            "profileId": 212695799,
            "extention": {
                "modelInfo": {
                    "compatibility": {
                        "devModelName": "C12",
                        "devProductName": "P1S",
                        "nozzleDiameter": 0.4,
                    },
                    "otherCompatibility": [
                        {"devModelName": "C11", "devProductName": "P1P", "nozzleDiameter": 0.4},
                        {"devModelName": "BL-P002", "devProductName": "X1", "nozzleDiameter": 0.4},
                        {
                            "devModelName": "BL-P001",
                            "devProductName": "X1 Carbon",
                            "nozzleDiameter": 0.4,
                        },
                        {"devModelName": "C13", "devProductName": "X1E", "nozzleDiameter": 0.4},
                        {"devModelName": "N2S", "devProductName": "A1", "nozzleDiameter": 0.4},
                        {"devModelName": "O1D", "devProductName": "H2D", "nozzleDiameter": 0.4},
                        {"devModelName": "O1E", "devProductName": "H2D Pro", "nozzleDiameter": 0.4},
                        {"devModelName": "O1S", "devProductName": "H2S", "nozzleDiameter": 0.4},
                        {"devModelName": "N7", "devProductName": "P2S", "nozzleDiameter": 0.4},
                        {"devModelName": "O1C2", "devProductName": "H2C", "nozzleDiameter": 0.4},
                        {"devModelName": "N6", "devProductName": "X2D", "nozzleDiameter": 0.4},
                        {"devModelName": "N9", "devProductName": "A2L", "nozzleDiameter": 0.4},
                        {"devModelName": "N10", "devProductName": "H3C", "nozzleDiameter": 0.4},
                    ],
                    "plates": [
                        {
                            "index": 1,
                            "name": "Plate 1",
                            "thumbnail": {
                                "name": "plate_1.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/212695799/instance/plate_1.png",
                            },
                            "prediction": 5151,
                            "weight": 57,
                        },
                        {
                            "index": 2,
                            "name": "",
                            "thumbnail": {
                                "name": "plate_2.png",
                                "url": "https://makerworld.bblmw.com/makerworld/model/USd4c4619315b7fc/212695799/instance/plate_2.png",
                            },
                            "prediction": 3666,
                            "weight": 31,
                        },
                    ],
                }
            },
        },
    ],
}
