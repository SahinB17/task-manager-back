from app import models


def create_activity_log(
    db,
    user_id,
    action,
    entity_type,
    entity_id=None,
    old_data=None,
    new_data=None
):
    log = models.ActivityLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_data=old_data,
        new_data=new_data
    )

    db.add(log)
