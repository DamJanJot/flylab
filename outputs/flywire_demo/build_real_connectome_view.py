import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from html.parser import HTMLParser
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DATA = Path.home() / "Downloads" / "mind"
CONNECTIONS = "connections_princeton.csv.gz"
MAX_CENTERS = 28
MAX_PER_SIDE = 24
SIM_PARTNERS = 12


class EmbeddedDataParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attributes = dict(attrs)
            self.capture = attributes.get("id") == "sim-data" and attributes.get("type") == "application/json"

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, content):
        if self.capture:
            self.parts.append(content)


def render_payload(payload, template_path, output_path):
    if not payload.get("centers") or not payload.get("nodes"):
        raise ValueError("Brak neuronow w osadzonych danych")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    template = template_path.read_text(encoding="utf-8")
    if template.count("__CONNECTOME_DATA__") != 1:
        raise ValueError("Szablon musi zawierac jeden znacznik danych")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template.replace("__CONNECTOME_DATA__", encoded), encoding="utf-8", newline="\n")
    print(f"Gotowe: {output_path}")
    print(f"Wiersze tabeli: {payload['rowCount']:,}; wybrane neurony: {len(payload['centers'])}")
    print(f"Rozmiar widoku: {output_path.stat().st_size:,} B")


def rows_from_gzip(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        yield from csv.DictReader(stream)


def load_metadata(data_dir):
    names = {row["root_id"]: row for row in rows_from_gzip(data_dir / "names.csv.gz")}
    neurons = {row["root_id"]: row for row in rows_from_gzip(data_dir / "neurons.csv.gz")}
    visual = {row["root_id"]: row for row in rows_from_gzip(data_dir / "visual_neuron_types.csv.gz")}
    return names, neurons, visual


def make_view(data_dir, template_path, output_path):
    connection_path = data_dir / CONNECTIONS
    if not connection_path.is_file():
        raise FileNotFoundError(f"Nie znaleziono pliku danych: {connection_path}")

    input_weight = Counter()
    output_weight = Counter()
    row_count = 0
    print("Czytam tabele polaczen...", flush=True)
    for row in rows_from_gzip(connection_path):
        pre = row["pre_root_id"]
        post = row["post_root_id"]
        weight = int(row["syn_count"])
        output_weight[pre] += weight
        input_weight[post] += weight
        row_count += 1

    names, neurons, visual = load_metadata(data_dir)
    score = Counter(input_weight)
    score.update(output_weight)
    ranked = sorted(score, key=score.get, reverse=True)

    selected = []
    selected_set = set()
    for root_id in ranked[:12]:
        selected.append(root_id)
        selected_set.add(root_id)

    best_by_visual_type = {}
    for root_id, row in visual.items():
        key = row.get("type") or row.get("family") or root_id
        current = best_by_visual_type.get(key)
        if root_id in score and (current is None or score[root_id] > score[current]):
            best_by_visual_type[key] = root_id

    visual_ranked = sorted(best_by_visual_type.values(), key=score.get, reverse=True)
    for root_id in visual_ranked:
        if root_id not in selected_set:
            selected.append(root_id)
            selected_set.add(root_id)
        if len(selected) >= MAX_CENTERS:
            break

    incoming = {root_id: Counter() for root_id in selected}
    outgoing = {root_id: Counter() for root_id in selected}
    print(f"Zbieram sasiedztwo dla {len(selected)} neuronow...", flush=True)
    for row in rows_from_gzip(connection_path):
        pre = row["pre_root_id"]
        post = row["post_root_id"]
        weight = int(row["syn_count"])
        if post in incoming and pre != post:
            incoming[post][pre] += weight
        if pre in outgoing and pre != post:
            outgoing[pre][post] += weight

    sim_nodes = {}
    memberships = defaultdict(set)
    for root_id in selected:
        node_ids = [root_id]
        node_ids.extend(partner for partner, _ in outgoing[root_id].most_common(SIM_PARTNERS))
        sim_nodes[root_id] = set(node_ids)
        for node_id in node_ids:
            memberships[node_id].add(root_id)

    sim_edges = {root_id: Counter() for root_id in selected}
    print("Buduje rzeczywiste krawedzie podgrafow symulacji...", flush=True)
    for row in rows_from_gzip(connection_path):
        pre = row["pre_root_id"]
        post = row["post_root_id"]
        if pre == post:
            continue
        pre_centers = memberships.get(pre)
        post_centers = memberships.get(post)
        if not pre_centers or not post_centers:
            continue
        shared_centers = pre_centers.intersection(post_centers)
        if not shared_centers:
            continue
        weight = int(row["syn_count"])
        for center_id in shared_centers:
            sim_edges[center_id][(pre, post)] += weight

    def details(root_id):
        name = names.get(root_id, {})
        neuron = neurons.get(root_id, {})
        visual_row = visual.get(root_id, {})
        return {
            "id": root_id,
            "name": name.get("name") or root_id,
            "group": name.get("group") or neuron.get("group") or "",
            "nt": neuron.get("nt_type") or "?",
            "visual": visual_row.get("type") or "",
            "family": visual_row.get("family") or "",
        }

    node_ids = set(selected)
    center_rows = []
    for root_id in selected:
        in_edges = incoming[root_id].most_common(MAX_PER_SIDE)
        out_edges = outgoing[root_id].most_common(MAX_PER_SIDE)
        node_ids.update(partner for partner, _ in in_edges)
        node_ids.update(partner for partner, _ in out_edges)
        center_rows.append({
            **details(root_id),
            "inTotal": int(input_weight[root_id]),
            "outTotal": int(output_weight[root_id]),
            "inEdges": [[partner, int(weight)] for partner, weight in in_edges],
            "outEdges": [[partner, int(weight)] for partner, weight in out_edges],
            "simNodes": [root_id, *[partner for partner, _ in outgoing[root_id].most_common(SIM_PARTNERS)]],
            "simEdges": [[pre, post, int(weight)] for (pre, post), weight in sim_edges[root_id].items()],
        })

    payload = {
        "source": "FlyWire FAFB v783, connections_princeton.csv.gz",
        "rowCount": row_count,
        "centers": center_rows,
        "nodes": {root_id: details(root_id) for root_id in node_ids},
    }
    render_payload(payload, template_path, output_path)


def main():
    parser = argparse.ArgumentParser(description="Buduje lokalny podglad prawdziwych polaczen FlyWire.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--reuse-data", type=Path, help="Odswiez widok z danych osadzonych w istniejacym HTML")
    parser.add_argument("--template", type=Path, default=HERE / "connectome-simulation.template.html")
    parser.add_argument("--output", type=Path, default=HERE / "connectome-simulation.html")
    args = parser.parse_args()
    if args.reuse_data:
        embedded = EmbeddedDataParser()
        embedded.feed(args.reuse_data.read_text(encoding="utf-8"))
        render_payload(json.loads("".join(embedded.parts)), args.template, args.output)
    else:
        make_view(args.data_dir, args.template, args.output)


if __name__ == "__main__":
    main()
