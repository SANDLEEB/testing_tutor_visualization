"""Renders a student's (or a class's) mastery as an actual graph — a radar/spider
chart, one spoke per topic, mastery as distance from center — instead of a bar list.

There's no edge/relationship data between Concepts (no prerequisite graph, nothing in
the schema linking one topic to another), so this deliberately doesn't draw a
node-and-edge topology — that would be fabricating structure that isn't there. A radar
chart is the honest "graph" for what we actually have: one mastery value per topic.
"""
import math
from html import escape

_TIER_COLOR = {'mastered': '#22c55e', 'partial': '#f97316', 'weak': '#ef4444'}
_MIN_POINTS_FOR_RADAR = 3  # a 1-2 axis radar is meaningless; callers should fall back to the list view


def can_render_radar(item_count: int) -> bool:
    return item_count >= _MIN_POINTS_FOR_RADAR


def render_mastery_radar(items: list[dict], *, size: int = 360) -> str:
    """items: [{'name': str, 'mastery': float 0..1, 'tier': 'mastered'|'partial'|'weak'}, ...]
    Returns a standalone inline <svg> string."""
    n = len(items)
    cx = cy = size / 2
    radius = size / 2 - 56  # leave room for topic labels around the edge

    def point(i: int, r_frac: float) -> tuple[float, float]:
        angle = -math.pi / 2 + i * (2 * math.pi / n)
        return cx + radius * r_frac * math.cos(angle), cy + radius * r_frac * math.sin(angle)

    def point_px(i: int, r_px: float) -> tuple[float, float]:
        """Like point(), but the radius is an absolute pixel offset from center —
        used for the percentage label so it sits a fixed distance from its dot
        regardless of how small or large that dot's mastery fraction is."""
        angle = -math.pi / 2 + i * (2 * math.pi / n)
        return cx + r_px * math.cos(angle), cy + r_px * math.sin(angle)

    rings = ''.join(
        '<polygon points="%s" fill="none" stroke="#e5e7eb" stroke-width="1"/>' % (
            ' '.join(f'{x:.1f},{y:.1f}' for x, y in (point(i, frac) for i in range(n)))
        )
        for frac in (0.25, 0.5, 0.75, 1.0)
    )

    spokes = []
    labels = []
    for i, item in enumerate(items):
        x, y = point(i, 1.0)
        spokes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e5e7eb" stroke-width="1"/>')
        lx, ly = point(i, 1.16)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="11" fill="#475569" '
            f'text-anchor="middle" dominant-baseline="middle">{escape(item["name"])}</text>'
        )
        dot_r_px = radius * item['mastery']
        pct_x, pct_y = point_px(i, dot_r_px + 16)  # fixed 16px clear of the dot, whatever its radius
        labels.append(
            f'<rect x="{pct_x - 16:.1f}" y="{pct_y - 8:.1f}" width="32" height="14" rx="3" fill="white" opacity="0.85"/>'
            f'<text x="{pct_x:.1f}" y="{pct_y:.1f}" font-size="10" font-weight="700" '
            f'fill="{_TIER_COLOR[item["tier"]]}" text-anchor="middle" dominant-baseline="middle">'
            f'{round(item["mastery"] * 100)}%</text>'
        )

    data_points = ' '.join(f'{x:.1f},{y:.1f}' for x, y in (point(i, items[i]['mastery']) for i in range(n)))
    polygon = f'<polygon points="{data_points}" fill="rgba(9,105,218,.14)" stroke="#0969da" stroke-width="2"/>'

    dots = ''.join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{_TIER_COLOR[items[i]["tier"]]}" '
        f'stroke="white" stroke-width="1.5"/>'
        for i, (x, y) in enumerate(point(i, items[i]['mastery']) for i in range(n))
    )

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'style="display:block;margin:0 auto">{rings}{"".join(spokes)}{polygon}{dots}{"".join(labels)}</svg>'
    )
