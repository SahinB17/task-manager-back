def test_task_create_derives_canonical_status_from_operational(
    client,
    auth_headers,
    factories,
    app_modules,
):
    models = app_modules["models"]
    factories["create_user"](
        full_name="Admin User",
        email="admin@test.local",
        role_id=1,
        password="adminpass",
    )

    # Create with operational_status only.
    response = client.post(
        "/tasks/",
        json={
            "title": "Status derived task",
            "operational_status": "active",
        },
        headers=auth_headers("admin@test.local", "adminpass"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["operational_status"] == "active"
    assert data["status"] == "in_progress", (
        f"Expected canonical status 'in_progress', got '{data['status']}'"
    )


def test_task_update_derives_canonical_status_from_operational(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin-derive@test.local",
        role_id=1,
        password="adminpass",
    )
    task = models.Task(title="Derive status", created_by=admin.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    # Update via operational_status → canonical status should follow.
    response = client.put(
        f"/tasks/{task.id}",
        json={"operational_status": "completed"},
        headers=auth_headers("admin-derive@test.local", "adminpass"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["operational_status"] == "completed"
    assert data["status"] == "done", (
        f"Expected canonical status 'done', got '{data['status']}'"
    )


def test_task_update_operational_wins_over_status(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    """When both operational_status and status are sent, operational_status wins."""
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin-conflict@test.local",
        role_id=1,
        password="adminpass",
    )
    task = models.Task(title="Conflict status", created_by=admin.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    # Send both — operational_status should win.
    response = client.put(
        f"/tasks/{task.id}",
        json={
            "operational_status": "completed",
            "status": "todo",  # Should be overwritten by operational_status logic
        },
        headers=auth_headers("admin-conflict@test.local", "adminpass"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["operational_status"] == "completed"
    assert data["status"] == "done", (
        f"Expected canonical 'done' (derived from completed), got '{data['status']}'"
    )


def test_task_update_does_not_show_duplicate_status_changes_in_notification(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    """When both status and operational_status change, only operational_status
    appears in the notification changes."""
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin Notif",
        email="admin-notif@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee Notif",
        email="employee-notif@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(
        title="Notif dedup",
        created_by=admin.id,
        assigned_to=employee.id,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    # Update operational_status — backend derives status, so both change.
    response = client.put(
        f"/tasks/{task.id}",
        json={"operational_status": "active"},
        headers=auth_headers("admin-notif@test.local", "adminpass"),
    )

    assert response.status_code == 200
    notification = db_session.query(models.Notification).one()
    fields_in_changes = [c["field"] for c in notification.metadata_["changes"]]

    assert "operational_status" in fields_in_changes
    assert "status" not in fields_in_changes, (
        "Canonical 'status' should be filtered out when operational_status also changed. "
        f"Got fields: {fields_in_changes}"
    )


def test_task_update_canonical_status_derives_operational(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    """When only canonical status is sent, the backend derives
    operational_status from it, and the notification shows both as a
    single operational_status change (deduped)."""
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin Canon",
        email="admin-canon@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee Canon",
        email="employee-canon@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(
        title="Canonical only",
        created_by=admin.id,
        assigned_to=employee.id,
    )
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    # Send only canonical status — backend derives operational_status.
    response = client.put(
        f"/tasks/{task.id}",
        json={"status": "in_progress"},
        headers=auth_headers("admin-canon@test.local", "adminpass"),
    )

    assert response.status_code == 200
    data = response.json()
    # Backend derives operational_status from canonical status.
    assert data["status"] == "in_progress"
    assert data["operational_status"] == "active"

    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    fields_in_changes = [c["field"] for c in notifications[0].metadata_["changes"]]
    # operational_status is derived, so status is deduped out.
    assert "status" not in fields_in_changes, (
        "Canonical status should be filtered out when operational_status was derived. "
        f"Got fields: {fields_in_changes}"
    )
    assert "operational_status" in fields_in_changes


def test_user_sees_only_own_notifications(client, auth_headers, factories, db_session, app_modules):
    models = app_modules["models"]
    user_one = factories["create_user"](
        full_name="User One",
        email="user-one@test.local",
        role_id=3,
        password="password123",
    )
    user_two = factories["create_user"](
        full_name="User Two",
        email="user-two@test.local",
        role_id=3,
        password="password123",
    )

    db_session.add_all([
        models.Notification(
            user_id=user_one.id,
            type="task_updated",
            title="Own notification",
            message="Own message",
        ),
        models.Notification(
            user_id=user_two.id,
            type="task_updated",
            title="Other notification",
            message="Other message",
        ),
    ])
    db_session.commit()

    response = client.get(
        "/notifications/",
        headers=auth_headers("user-one@test.local", "password123"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["title"] == "Own notification"


def test_unread_count_returns_only_current_user_unread(client, auth_headers, factories, db_session, app_modules):
    models = app_modules["models"]
    user_one = factories["create_user"](
        full_name="User One",
        email="user-one@test.local",
        role_id=3,
        password="password123",
    )
    user_two = factories["create_user"](
        full_name="User Two",
        email="user-two@test.local",
        role_id=3,
        password="password123",
    )

    db_session.add_all([
        models.Notification(user_id=user_one.id, type="a", title="Unread", message="Unread"),
        models.Notification(user_id=user_one.id, type="a", title="Read", message="Read", is_read=True),
        models.Notification(user_id=user_two.id, type="a", title="Other", message="Other"),
    ])
    db_session.commit()

    response = client.get(
        "/notifications/unread-count",
        headers=auth_headers("user-one@test.local", "password123"),
    )

    assert response.status_code == 200
    assert response.json()["unread_count"] == 1


def test_mark_one_notification_as_read(client, auth_headers, factories, db_session, app_modules):
    models = app_modules["models"]
    user = factories["create_user"](
        full_name="User One",
        email="user-one@test.local",
        role_id=3,
        password="password123",
    )
    notification = models.Notification(
        user_id=user.id,
        type="task_updated",
        title="Unread",
        message="Unread",
    )
    db_session.add(notification)
    db_session.commit()
    db_session.refresh(notification)

    response = client.patch(
        f"/notifications/{notification.id}/read",
        headers=auth_headers("user-one@test.local", "password123"),
    )

    assert response.status_code == 200
    assert response.json()["is_read"] is True
    assert response.json()["read_at"] is not None


def test_mark_all_notifications_as_read(client, auth_headers, factories, db_session, app_modules):
    models = app_modules["models"]
    user = factories["create_user"](
        full_name="User One",
        email="user-one@test.local",
        role_id=3,
        password="password123",
    )
    other_user = factories["create_user"](
        full_name="Other User",
        email="other@test.local",
        role_id=3,
        password="password123",
    )
    db_session.add_all([
        models.Notification(user_id=user.id, type="a", title="One", message="One"),
        models.Notification(user_id=user.id, type="a", title="Two", message="Two"),
        models.Notification(user_id=other_user.id, type="a", title="Other", message="Other"),
    ])
    db_session.commit()

    response = client.patch(
        "/notifications/read-all",
        headers=auth_headers("user-one@test.local", "password123"),
    )

    assert response.status_code == 200
    assert response.json()["updated_count"] == 2
    assert db_session.query(models.Notification).filter(
        models.Notification.user_id == user.id,
        models.Notification.is_read.is_(False),
    ).count() == 0
    assert db_session.query(models.Notification).filter(
        models.Notification.user_id == other_user.id,
        models.Notification.is_read.is_(False),
    ).count() == 1


def test_task_assigned_creates_notification_for_assigned_user(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    factories["create_user"](
        full_name="Admin User",
        email="admin@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee@test.local",
        role_id=3,
        password="employeepass",
    )

    response = client.post(
        "/tasks/",
        json={"title": "Assigned task", "assigned_to": str(employee.id)},
        headers=auth_headers("admin@test.local", "adminpass"),
    )

    assert response.status_code == 200
    notification = db_session.query(models.Notification).one()
    assert notification.user_id == employee.id
    assert notification.type == "task_assigned"
    assert notification.title == "Yeni tapşırıq"

    list_response = client.get(
        "/notifications/",
        headers=auth_headers("employee@test.local", "employeepass"),
    )

    assert list_response.status_code == 200
    item = list_response.json()["items"][0]
    assert item["actor"]["full_name"] == "Admin User"
    assert item["task"]["title"] == "Assigned task"


def test_task_update_by_another_user_notifies_assigned_user(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(title="Update me", created_by=admin.id, assigned_to=employee.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    response = client.put(
        f"/tasks/{task.id}",
        json={"priority": "high"},
        headers=auth_headers("admin@test.local", "adminpass"),
    )

    assert response.status_code == 200
    notification = db_session.query(models.Notification).one()
    assert notification.user_id == employee.id
    assert notification.type == "task_updated"
    assert notification.metadata_["changes"] == [
        {"field": "priority", "old": "medium", "new": "high"}
    ]


def test_task_update_by_assigned_user_notifies_creator_but_not_themselves(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(title="Own update", created_by=admin.id, assigned_to=employee.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    response = client.put(
        f"/tasks/{task.id}",
        json={"status": "in_progress"},
        headers=auth_headers("employee@test.local", "employeepass"),
    )

    assert response.status_code == 200
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == admin.id
    assert notifications[0].type == "task_updated"


def test_task_completed_notifies_relevant_users_except_actor(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(title="Complete me", created_by=admin.id, assigned_to=employee.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    response = client.put(
        f"/tasks/{task.id}",
        json={"status": "done"},
        headers=auth_headers("employee@test.local", "employeepass"),
    )

    assert response.status_code == 200
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == admin.id
    assert {item.type for item in notifications} == {"task_completed"}
    assert db_session.query(models.Notification).filter(
        models.Notification.user_id == employee.id
    ).count() == 0


def test_user_cannot_mark_another_users_notification_as_read(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    user_one = factories["create_user"](
        full_name="User One",
        email="user-one@test.local",
        role_id=3,
        password="password123",
    )
    user_two = factories["create_user"](
        full_name="User Two",
        email="user-two@test.local",
        role_id=3,
        password="password123",
    )
    notification = models.Notification(
        user_id=user_two.id,
        type="task_updated",
        title="Other",
        message="Other",
    )
    db_session.add(notification)
    db_session.commit()
    db_session.refresh(notification)

    response = client.patch(
        f"/notifications/{notification.id}/read",
        headers=auth_headers("user-one@test.local", "password123"),
    )

    assert response.status_code == 404
    db_session.refresh(notification)
    assert notification.is_read is False


def test_task_status_update_notifies_creator_when_assignee_changes_status(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin-status@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee-status@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(title="Status change", created_by=admin.id, assigned_to=employee.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    response = client.put(
        f"/tasks/{task.id}",
        json={"operational_status": "active"},
        headers=auth_headers("employee-status@test.local", "employeepass"),
    )

    assert response.status_code == 200
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == admin.id
    assert notifications[0].type == "task_updated"


def test_task_completion_does_not_create_duplicate_update_notification(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin-complete@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee-complete@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(title="Only completion", created_by=admin.id, assigned_to=employee.id)
    db_session.add(task)
    db_session.commit()
    db_session.refresh(task)

    response = client.put(
        f"/tasks/{task.id}",
        json={"status": "done"},
        headers=auth_headers("admin-complete@test.local", "adminpass"),
    )

    assert response.status_code == 200
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].user_id == employee.id
    assert notifications[0].type == "task_completed"


def test_notifications_endpoint_creates_deadline_reminder_and_avoids_duplicates(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    from datetime import datetime, timedelta

    models = app_modules["models"]
    employee = factories["create_user"](
        full_name="Deadline User",
        email="deadline@test.local",
        role_id=3,
        password="password123",
    )
    task = models.Task(
        title="Soon task",
        created_by=employee.id,
        assigned_to=employee.id,
        deadline=datetime.now() + timedelta(days=2),
    )
    db_session.add(task)
    db_session.commit()

    first_response = client.get(
        "/notifications/",
        headers=auth_headers("deadline@test.local", "password123"),
    )
    second_response = client.get(
        "/notifications/",
        headers=auth_headers("deadline@test.local", "password123"),
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].type == "task_deadline_reminder"


def test_unread_count_creates_overdue_notification_for_relevant_user(
    client,
    auth_headers,
    factories,
    db_session,
    app_modules,
):
    from datetime import datetime, timedelta

    models = app_modules["models"]
    admin = factories["create_user"](
        full_name="Admin User",
        email="admin-overdue@test.local",
        role_id=1,
        password="adminpass",
    )
    employee = factories["create_user"](
        full_name="Employee User",
        email="employee-overdue@test.local",
        role_id=3,
        password="employeepass",
    )
    task = models.Task(
        title="Late task",
        created_by=admin.id,
        assigned_to=employee.id,
        deadline=datetime.now() - timedelta(days=1),
    )
    db_session.add(task)
    db_session.commit()

    response = client.get(
        "/notifications/unread-count",
        headers=auth_headers("employee-overdue@test.local", "employeepass"),
    )

    assert response.status_code == 200
    assert response.json()["unread_count"] == 1
    notifications = db_session.query(models.Notification).all()
    assert len(notifications) == 1
    assert notifications[0].type == "task_overdue"
    assert notifications[0].user_id == employee.id
