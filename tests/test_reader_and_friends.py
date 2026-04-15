from datetime import datetime, timezone


def test_reader_defaults_to_unread_only(client, login_local):
    login_local("reader-user")

    response = client.get("/reader")

    assert response.status_code == 200
    assert "let unreadOnly = true;" in response.text


def test_reverse_pending_friend_request_points_user_to_profile(
    app_module, client, db, login_local
):
    alice = app_module.User(
        email="alice@example.com",
        name="Alice",
        password_hash=app_module.hash_password("password123"),
    )
    bob = app_module.User(
        email="bob@example.com",
        name="Bob",
        password_hash=app_module.hash_password("password123"),
    )
    db.add_all([alice, bob])
    db.commit()
    db.refresh(alice)
    db.refresh(bob)

    db.add(
        app_module.Friendship(
            requester_user_id=alice.id,
            addressee_user_id=bob.id,
            status="pending",
        )
    )
    db.commit()

    login_local("bob@example.com")
    response = client.post("/api/friends/request", data={"email": "alice@example.com"})

    assert response.status_code == 409
    assert response.json()["direction"] == "incoming"
    assert "Profile" in response.json()["error"]


def test_profile_page_renders_friendship_sections(app_module, client, db, login_local):
    owner = app_module.User(
        email="owner@example.com",
        name="Owner",
        password_hash=app_module.hash_password("password123"),
    )
    accepted_friend = app_module.User(
        email="friend@example.com",
        name="Friend Person",
    )
    incoming_friend = app_module.User(
        email="incoming@example.com",
        name="Incoming Person",
    )
    outgoing_friend = app_module.User(
        email="outgoing@example.com",
        name="Outgoing Person",
    )
    db.add_all([owner, accepted_friend, incoming_friend, outgoing_friend])
    db.commit()
    db.refresh(owner)
    db.refresh(accepted_friend)
    db.refresh(incoming_friend)
    db.refresh(outgoing_friend)

    db.add_all(
        [
            app_module.Friendship(
                requester_user_id=owner.id,
                addressee_user_id=accepted_friend.id,
                status="accepted",
                accepted_at=datetime.now(timezone.utc),
            ),
            app_module.Friendship(
                requester_user_id=incoming_friend.id,
                addressee_user_id=owner.id,
                status="pending",
            ),
            app_module.Friendship(
                requester_user_id=owner.id,
                addressee_user_id=outgoing_friend.id,
                status="pending",
            ),
        ]
    )
    db.commit()

    login_local("owner@example.com")
    response = client.get("/reader/profile")

    assert response.status_code == 200
    assert "Pending requests" in response.text
    assert "Friends" in response.text
    assert "Friend Person" in response.text
    assert "Incoming Person" in response.text
    assert "Outgoing Person" in response.text
