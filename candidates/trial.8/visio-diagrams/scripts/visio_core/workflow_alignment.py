"""Derive shared tracks for sibling branches from intent and main edges."""
from collections import defaultdict


def workflow_rules(original, ranks):
    """Determine comparable frames and axes without reading rendered geometry."""
    result = {"spine": [], "columns": [], "peerSizes": [], "strips": []}
    layout = original.get("layout", {})
    nodes = {n["id"]: n for n in original["nodes"]}
    # Group frames and authored locations have their own placement systems.
    if (layout.get("mode") != "intent" or layout.get("groups")
            or any(n.get("kind") == "container" or "position" in n
                   or "position" in n.get("draft", {}) for n in nodes.values())):
        return result

    parents, children = defaultdict(set), defaultdict(set)
    for edge in original["edges"]:
        if edge.get("role", "main") == "main":
            a, b = edge["from"], edge["to"]
            children[a].add(b)
            parents[b].add(a)
    parallel = layout.get("parallel", [])
    declared = {frozenset(peers) for peers in parallel}
    rank_index = 0
    while rank_index < len(ranks):
        row = ranks[rank_index]
        rank_index += 1
        if len(row) < 2:
            continue
        members = frozenset(row)
        if members not in declared and not any(children[p] == members for p in parents[row[0]]):
            continue

        strip = [row]
        while rank_index < len(ranks) and len(ranks[rank_index]) == len(row):
            # Follow an entire rank only when every track has an unambiguous
            # one-to-one continuation. A fork, join or partial rank ends it.
            if any(len(children[n]) != 1 for n in row):
                break
            following = [next(iter(children[n])) for n in row]
            if (set(following) != set(ranks[rank_index])
                    or any(len(parents[n]) != 1 for n in following)):
                break
            positions = {n: i for i, n in enumerate(following)}
            if any(positions[a] >= positions[b] for peers in parallel
                   for a, b in zip(peers, peers[1:]) if a in positions and b in positions):
                break
            strip.append(following)
            row = following
            rank_index += 1

        result["strips"].append(strip)
        if len(strip) > 1:
            result["columns"].extend(list(col) for col in zip(*strip))
        by_kind = defaultdict(list)
        for branch_row in strip:
            for key in branch_row:
                node = nodes[key]
                if "size" not in node and "size" not in node.get("draft", {}) and node.get("kind") != "text":
                    by_kind[node.get("kind", "box")].append(key)
        result["peerSizes"].extend(peers for peers in by_kind.values() if len(peers) > 1)

    # Relate the middle track only to single-node ranks in the same workflow.
    middles = {row[len(row)//2] for strip in result["strips"] for row in strip if len(row) % 2}
    connected = {n for strip in result["strips"] for row in strip for n in row}
    pending = list(connected)
    while pending:
        key = pending.pop()
        new = (children[key] | parents[key]) - connected
        connected.update(new)
        pending.extend(new)
    result["spine"] = [key for row in ranks for key in row
                       if key in middles or (len(row) == 1 and key in connected)]
    return result


def apply_workflow_tracks(items, ranks, rules, horizontal):
    """Grow automatic peers and reserve track space independent of visible size."""
    for peers in rules["peerSizes"]:
        w = max(items[n]["w"] for n in peers)
        h = max(items[n]["h"] for n in peers)
        for n in peers:
            items[n].update(w=w, h=h)

    cross = "h" if horizontal else "w"
    slots = {}
    order = {}
    for strip in rules["strips"]:
        widths = [max(items[n][cross] for n in col) for col in zip(*strip)]
        for row in strip:
            for index, n in enumerate(row):
                slots[n] = widths[index]
                order[n] = index
    return [sorted(row, key=order.__getitem__) if row[0] in order else row for row in ranks], slots
