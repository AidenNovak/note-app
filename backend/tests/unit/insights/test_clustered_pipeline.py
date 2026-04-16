from __future__ import annotations

from app.intelligence.insights.clustered_pipeline import _build_fallback_angles
from app.intelligence.insights.graph_clustering import NoteCluster


def test_build_fallback_angles_creates_deterministic_angles():
    clusters = [
        NoteCluster(
            cluster_id=0,
            note_ids=["n1", "n2", "n3"],
            shared_tags=["writing", "draft"],
        ),
        NoteCluster(
            cluster_id=1,
            note_ids=["n4"],
            shared_tags=["research"],
        ),
    ]

    angles = _build_fallback_angles(clusters)

    assert len(angles) == 2
    assert angles[0].angle_name == "writing / draft"
    assert angles[0].type_hint == "pattern"
    assert angles[0].note_ids == ["n1", "n2", "n3"]
    assert angles[1].angle_name == "research"
