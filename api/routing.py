"""Turns a list of customers with known map locations into a weekly visit plan.

No external routing service — Riyadh's customers are close enough together
that straight-line (great-circle) distance is a fine proxy for "worth visiting
back to back," and it means this works with zero API keys and zero cost.

The algorithm is greedy nearest-neighbour, seeded each day by whichever
unplanned customer is most urgent: start the day at the most pressing account,
then keep adding whichever unplanned customer is closest to the last stop,
until either the day is full or the next-closest customer is too far away to
be worth the detour — at which point a new day starts, seeded the same way.
"""

import math

EARTH_RADIUS_KM = 6371.0

#: Stop adding to a day's route once the next-nearest customer is farther than
#: this from the last stop. Keeps a day's route to one part of the city rather
#: than zig-zagging Riyadh end to end chasing the single most urgent account.
MAX_HOP_KM = 12.0

#: Stops the day is not turned into a marathon.
MAX_PER_DAY = 8


def haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(1.0, a)))


def priority_score(customer):
    """Higher = more worth a special trip. Broken promises and bigger overdue
    balances float to the top of the queue; a customer merely "not yet due"
    contributes nothing so the plan stays about who actually needs chasing."""
    score = customer.get('overdue_total', 0.0) or 0.0
    if customer.get('status') == 'promised' and customer.get('promise_broken'):
        score += 200_000  # a broken promise jumps the queue over raw amount
    if customer.get('needs_visit'):
        score += 100_000
    return score


def suggest_plan(customers, days=6, max_per_day=MAX_PER_DAY, max_hop_km=MAX_HOP_KM):
    """Greedy nearest-neighbour clustering into `days` day-buckets.

    Args:
        customers: dicts with at least partner_id, lat, lng, and whatever
            priority_score() reads. Only ones with both lat and lng set are
            plannable; the rest are returned separately so the caller can
            tell the collector who still needs a pin dropped.
        days: how many days to spread visits across.
        max_per_day: stop count cap per day.
        max_hop_km: distance beyond which a new day starts instead of
            tacking the next customer onto the current route.

    Returns:
        (plan, unplaced) — plan is a list of `days` lists of customer dicts
        (each carrying a `hop_km` from the previous stop, 0 for the first);
        unplaced is customers with no location set, most-urgent first.
    """
    plannable = [c for c in customers if c.get('lat') is not None and c.get('lng') is not None]
    unplaced = sorted(
        (c for c in customers if c.get('lat') is None or c.get('lng') is None),
        key=lambda c: -priority_score(c),
    )

    remaining = sorted(plannable, key=lambda c: -priority_score(c))
    plan = [[] for _ in range(days)]
    day = 0
    while remaining and day < days:
        seed = remaining.pop(0)
        plan[day].append({**seed, 'hop_km': 0.0})
        while remaining and len(plan[day]) < max_per_day:
            last = plan[day][-1]
            remaining.sort(key=lambda c: haversine_km(last['lat'], last['lng'], c['lat'], c['lng']))
            nearest = remaining[0]
            hop = haversine_km(last['lat'], last['lng'], nearest['lat'], nearest['lng'])
            if hop > max_hop_km:
                break
            plan[day].append({**remaining.pop(0), 'hop_km': round(hop, 1)})
        day += 1

    # Ran out of days before running out of urgent customers: rather than
    # drop them, pile the leftovers onto whichever day is currently shortest,
    # so nobody who needs a visit silently disappears from the plan.
    for c in remaining:
        shortest = min(range(days), key=lambda d: len(plan[d]))
        plan[shortest].append({**c, 'hop_km': None})

    return plan, unplaced
