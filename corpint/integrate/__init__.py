from collections import defaultdict
from itertools import combinations
from sqlalchemy import Unicode
import Levenshtein
import fingerprints
from corpint.integrate.merge import merge_entities, merge_links  # noqa
from corpint.integrate.dupe import create_deduper, train_judgement
from corpint.integrate.dupe import pairwise_score, to_record  # noqa
# from corpint.integrate.dupe import canonicalise
from corpint.integrate.util import normalize_name, get_clusters
from corpint.integrate.util import get_decided, merkle, sorttuple
from corpint.util import ensure_column


def name_merge(project, origins):
    by_name = defaultdict(set)
    for entity in project.entities:
        if len(origins) and entity['origin'] not in origins:
            continue
        name = normalize_name(entity['name'])
        by_name[name].add(entity['uid'])

    for name, uids in by_name.items():
        if len(uids) == 1:
            continue

        project.log.info("Merge: %s (%d matches)", name, len(uids))
        for (left, right) in combinations(uids, 2):
            project.emit_judgement(left, right, True)


def generate_candidates(project, threshold=.5):
    deduper, data = create_deduper(project)
    decided = get_decided(project)
    project.mappings.delete(judgement=None)
    for ((left_uid, left), (right_uid, right)) in combinations(data.items(), 2):
        if sorttuple(left_uid, right_uid) in decided:
            continue
        score = pairwise_score(project, deduper, left, right)
        if score <= threshold:
            continue
        project.log.info("Candidate [%.3f]: %s <-> %s", score, left['name'], right['name'])
        project.emit_judgement(left_uid, right_uid, judgement=None, score=score)


def generate_candidates_simple(project, threshold=.5):
    data = {e['uid']: to_record(e) for e in project.entities}
    decided = get_decided(project)
    # project.mappings.delete(judgement=None)
    for ((left_uid, left), (right_uid, right)) in combinations(data.items(), 2):
        if sorttuple(left_uid, right_uid) in decided:
            continue
        left_name = fingerprints.generate(left.get('name'))
        right_name = fingerprints.generate(right.get('name'))
        distance = Levenshtein.distance(left_name, right_name)
        score = 1 - (distance / float(max(len(left_name), len(right_name))))
        if score <= threshold:
            continue
        project.log.info("Candidate [%.3f]: %s <-> %s", score, left['name'], right['name'])
        project.emit_judgement(left_uid, right_uid, judgement=None, score=score)


def canonicalise(project):
    updates = (
        (project.entities, 'uid', 'uid_canonical'),
        (project.aliases, 'uid', 'uid_canonical'),
        (project.links, 'source', 'source_canonical'),
        (project.links, 'target', 'target_canonical'),
    )

    for (table, src, dest) in updates:
        table.create_index([src])
        ensure_column(table, dest, Unicode)
        table.create_index([dest])
        project.db.query("UPDATE %s SET %s = %s;" % (table.table.name, dest, src))  # noqa

    clusters = get_clusters(project)
    project.log.info("Canonicalise: %d clusters", len(clusters))
    for uids in clusters:
        canon = merkle(uids)
        uids = ', '.join(["'%s'" % u for u in uids])
        for (table, src, dest) in updates:
            query = "UPDATE %s SET %s = '%s' WHERE %s IN (%s)"
            query = query % (table.table.name, dest, canon, src, uids)
            project.db.query(query)
