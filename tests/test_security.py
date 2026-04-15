def test_add_feed_rejects_localhost_url(client, login_local):
    login_local("security-user")

    response = client.post(
        "/api/feeds",
        data={"url": "http://127.0.0.1:8765/internal-check"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Feed URL is not allowed"


def test_opml_import_skips_localhost_feed_url(client, login_local):
    login_local("security-user")

    opml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <body>
    <outline text="Private feed" type="rss" xmlUrl="http://127.0.0.1:8765/private.xml" />
  </body>
</opml>
"""

    response = client.post(
        "/api/opml/import",
        files={"file": ("feeds.opml", opml, "application/xml")},
    )

    assert response.status_code == 200
    assert response.json()["imported"] == 0
    assert response.json()["skipped"] == 1


def test_opml_import_rejects_malformed_xml(client, login_local):
    login_local("security-user")

    response = client.post(
        "/api/opml/import",
        files={
            "file": (
                "broken.opml",
                "<opml><body><outline></body>",
                "application/xml",
            )
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "Invalid OPML file"


def test_background_fetch_refuses_non_public_source(app_module, db):
    source = app_module.FeedSource(
        normalized_url="http://127.0.0.1:8765/private.xml",
        display_url="http://127.0.0.1:8765/private.xml",
        site_url="http://127.0.0.1:8765/private.xml",
        title="Private feed",
    )
    db.add(source)
    db.commit()
    db.refresh(source)

    result = app_module.fetch_source_articles(db, source)
    db.refresh(source)

    assert result["success"] is False
    assert result["error"] == "Feed URL is not allowed"
    assert source.fetch_status == "error"
    assert source.fetch_error == "Feed URL is not allowed"
