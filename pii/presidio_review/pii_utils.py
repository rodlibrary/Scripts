"""Cleanup of raw Presidio results, shared by text- and column-level analysis."""


def drop_nested(results):
    """Remove a finding fully inside another finding with an equal or higher score,
    e.g. the 'example.com' URL Presidio also reports inside an email address."""
    kept = []
    for r in results:
        contained = any(
            o is not r and o.start <= r.start and r.end <= o.end
            and (o.end - o.start) > (r.end - r.start) and o.score >= r.score
            for o in results
        )
        if not contained:
            kept.append(r)
    return kept


def collapse_same_span(results):
    """Presidio can flag the exact same text twice (e.g. PHONE_NUMBER and UK_NHS).
    Returns [(best_result, [other entity types])], one per distinct span."""
    best, others = {}, {}
    for r in sorted(results, key=lambda r: -r.score):
        key = (r.start, r.end)
        if key in best:
            others[key].append(r.entity_type)
        else:
            best[key], others[key] = r, []
    return [(r, others[k]) for k, r in best.items()]


def clean(results, keep_nested=False):
    """drop_nested (optional) + one result per span."""
    if not keep_nested:
        results = drop_nested(results)
    return [r for r, _ in collapse_same_span(results)]
