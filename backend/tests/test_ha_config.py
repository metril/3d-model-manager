"""P8 Home Assistant plan Task 1: ``app.services.ha_config``. Mirrors
``tests/test_secrets_at_rest.py``'s storage/import-token coverage -- the
two secrets (``ha_mqtt_password``/``ha_token``) are Fernet-encrypted at
rest with the same sentinel convention (omitted/``"***"`` keeps, ``""``
clears), and a legacy plaintext row still reads correctly and gets
eagerly re-encrypted by ``app.services.secrets_at_rest``.
"""

import pytest

from app.config import get_settings
from app.crypto import encrypt_secret
from app.models import Setting
from app.services import ha_config
from app.services.secrets_at_rest import reencrypt_secrets_at_rest

# ---------------------------------------------------------------------------
# parse_mqtt_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("mqtt://broker.local", ("broker.local", 1883, False)),
        ("mqtts://broker.local", ("broker.local", 8883, True)),
        ("mqtt://broker.local:1884", ("broker.local", 1884, False)),
        ("mqtts://broker.local:8884", ("broker.local", 8884, True)),
    ],
)
def test_parse_mqtt_url_matrix(url, expected):
    assert ha_config.parse_mqtt_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://broker.local",  # wrong scheme
        "ws://broker.local",
        "mqtt://user:pass@broker.local",  # userinfo not supported
        "mqtt://",  # no host
    ],
)
def test_parse_mqtt_url_rejects_bad_urls(url):
    with pytest.raises(ValueError):
        ha_config.parse_mqtt_url(url)


# ---------------------------------------------------------------------------
# get/set round trip + secrets at rest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_with_no_row_returns_defaults(db_session):
    config = await ha_config.get_ha_config(db_session, get_settings())
    assert config.ha_mqtt_url is None
    assert config.ha_mqtt_password is None
    assert config.ha_token is None
    assert config.ha_discovery_prefix == "homeassistant"


@pytest.mark.asyncio
async def test_secrets_encrypted_at_rest(db_session):
    settings = get_settings()
    await ha_config.set_ha_config(
        db_session,
        settings,
        {
            "ha_mqtt_url": "mqtt://broker.local",
            "ha_mqtt_user": "mqttuser",
            "ha_mqtt_password": "mqttsecret",
            "ha_url": "http://homeassistant.local:8123",
            "ha_token": "longlivedtoken",
        },
    )
    row = await db_session.get(Setting, "home_assistant")
    assert row.value["ha_mqtt_password_enc"] != "mqttsecret"
    assert row.value["ha_token_enc"] != "longlivedtoken"
    assert "mqttsecret" not in str(row.value)
    assert "longlivedtoken" not in str(row.value)

    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "mqttsecret"
    assert config.ha_token == "longlivedtoken"
    assert config.ha_mqtt_url == "mqtt://broker.local"
    assert config.ha_mqtt_user == "mqttuser"
    assert config.ha_url == "http://homeassistant.local:8123"


@pytest.mark.asyncio
async def test_sentinel_keeps_secret(db_session):
    settings = get_settings()
    await ha_config.set_ha_config(
        db_session, settings, {"ha_mqtt_password": "first", "ha_token": "firsttoken"}
    )

    # an omitted key keeps the stored value...
    await ha_config.set_ha_config(db_session, settings, {"ha_mqtt_url": "mqtt://new.local"})
    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "first"
    assert config.ha_token == "firsttoken"
    assert config.ha_mqtt_url == "mqtt://new.local"

    # ...and so does the explicit "***" sentinel.
    await ha_config.set_ha_config(
        db_session, settings, {"ha_mqtt_password": "***", "ha_token": "***"}
    )
    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "first"
    assert config.ha_token == "firsttoken"


@pytest.mark.asyncio
async def test_empty_clears(db_session):
    settings = get_settings()
    await ha_config.set_ha_config(
        db_session, settings, {"ha_mqtt_password": "first", "ha_token": "firsttoken"}
    )

    await ha_config.set_ha_config(db_session, settings, {"ha_mqtt_password": "", "ha_token": ""})
    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password is None
    assert config.ha_token is None


@pytest.mark.asyncio
async def test_set_overwrites_a_changed_secret(db_session):
    settings = get_settings()
    await ha_config.set_ha_config(db_session, settings, {"ha_mqtt_password": "first"})
    await ha_config.set_ha_config(db_session, settings, {"ha_mqtt_password": "second"})
    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "second"


def test_repr_secret_never_shown():
    config = ha_config.HaConfig(
        ha_mqtt_url="mqtt://broker.local",
        ha_mqtt_user="mqttuser",
        ha_mqtt_password="supersecret",
        ha_url="http://homeassistant.local:8123",
        ha_token="longlivedtoken",
    )
    text = repr(config)
    assert "supersecret" not in text
    assert "longlivedtoken" not in text
    assert text.count("***") == 2


def test_repr_masks_absent_secrets_as_none():
    config = ha_config.HaConfig(
        ha_mqtt_url=None, ha_mqtt_user=None, ha_mqtt_password=None, ha_url=None, ha_token=None
    )
    text = repr(config)
    assert "***" not in text
    assert "ha_mqtt_password=None" in text
    assert "ha_token=None" in text


def test_mqtt_signature_tuple():
    config = ha_config.HaConfig(
        ha_mqtt_url="mqtt://broker.local",
        ha_mqtt_user="u",
        ha_mqtt_password="p",
        ha_url=None,
        ha_token=None,
        ha_discovery_prefix="custom",
    )
    assert config.mqtt_signature() == ("mqtt://broker.local", "u", "p", "custom")


@pytest.mark.asyncio
async def test_legacy_plaintext_home_assistant_row_reads_and_gets_reencrypted(db_session):
    settings = get_settings()
    db_session.add(
        Setting(
            key="home_assistant",
            value={"ha_mqtt_password_enc": "plaintextpw", "ha_token_enc": "plaintexttoken"},
        )
    )
    await db_session.commit()

    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "plaintextpw"
    assert config.ha_token == "plaintexttoken"

    await reencrypt_secrets_at_rest(db_session, settings)

    row = await db_session.get(Setting, "home_assistant")
    assert row.value["ha_mqtt_password_enc"] != "plaintextpw"
    assert row.value["ha_token_enc"] != "plaintexttoken"

    config = await ha_config.get_ha_config(db_session, settings)
    assert config.ha_mqtt_password == "plaintextpw"
    assert config.ha_token == "plaintexttoken"


@pytest.mark.asyncio
async def test_reencrypt_home_assistant_pass_is_idempotent(db_session):
    settings = get_settings()
    db_session.add(
        Setting(
            key="home_assistant",
            value={
                "ha_mqtt_password_enc": encrypt_secret(settings, "already-ciphertext"),
                "ha_token_enc": "plaintexttoken",
            },
        )
    )
    await db_session.commit()

    await reencrypt_secrets_at_rest(db_session, settings)
    row = await db_session.get(Setting, "home_assistant")
    password_ciphertext = row.value["ha_mqtt_password_enc"]
    token_ciphertext = row.value["ha_token_enc"]

    await reencrypt_secrets_at_rest(db_session, settings)
    row_again = await db_session.get(Setting, "home_assistant")
    assert row_again.value["ha_mqtt_password_enc"] == password_ciphertext
    assert row_again.value["ha_token_enc"] == token_ciphertext


@pytest.mark.asyncio
async def test_sync_twin_get(db_session):
    from app.tasks import base

    settings = get_settings()
    await ha_config.set_ha_config(db_session, settings, {"ha_mqtt_url": "mqtt://sync.local"})

    with base.sync_session() as s:
        config = ha_config.get_ha_config_sync(s, settings)
    assert config.ha_mqtt_url == "mqtt://sync.local"
