"""Small, pure authorization policy for claim workspaces."""

ROLE_PERMISSIONS = {
    "EMPLOYEE": ("create_claims",),
    "APPROVER": ("create_claims", "review_claims", "mark_paid"),
}


def permissions_for(role):
    return list(ROLE_PERMISSIONS.get(role, ()))


def can_view(user, claim):
    return claim["owner_id"] == user["id"] or (
        "review_claims" in user["permissions"]
        and (claim["status"] != "DRAFT" or claim["has_review_history"])
    )


def allowed_actions(user, claim):
    permissions = user["permissions"]
    status = claim["status"]
    if claim["owner_id"] == user["id"]:
        if "create_claims" not in permissions:
            return []
        if status == "DRAFT":
            return ["edit"] + ([] if claim["has_submission_history"] else ["delete"]) + ["submit"]
        return ["edit", "submit"] if status == "REJECTED" else []
    if status == "SUBMITTED" and "review_claims" in permissions:
        return ["review"]
    if status == "APPROVED" and "mark_paid" in permissions:
        return ["pay"]
    return []
