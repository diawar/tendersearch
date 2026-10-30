"""Subject filtering uses source classifications, never keywords in the title."""


def subject_ids(row):
    def known(values):
        if not isinstance(values, (list, tuple)):
            values = [values]
        result = set()
        for value in values:
            if isinstance(value, dict):
                value = value.get('id')
            if not isinstance(value, bool) and str(value) in {'1', '2', '3'}:
                result.add(str(value))
        return result

    result = known(row.get('purchase_subject_id'))
    if result:
        return result
    return known([plan.get('purchase_subject') for plan in row.get('plan_items') or [] if isinstance(plan, dict)])


def subject_matches(row, selected):
    subjects = subject_ids(row)
    # Unknown subjects are deliberately included, but all other filters still apply.
    return not selected or not subjects or str(selected) in subjects
