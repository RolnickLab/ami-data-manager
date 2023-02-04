# import newrelic.agent
# newrelic.agent.initialize(environment="staging")

import sys
import os
import tempfile
import pathlib

from sqlalchemy import select, orm
from PIL import Image
import torch
from rich import print

from trapdata import logger
from trapdata import constants
from trapdata.db import get_db, check_db, get_session_class
from trapdata.db.models.events import (
    get_or_create_monitoring_sessions,
    MonitoringSession,
)
from trapdata.db.models.images import TrapImage
from trapdata.db.models.queue import add_image_to_queue, clear_all_queues
from trapdata.db.models.detections import DetectedObject, get_detections_for_image

from trapdata.ml.utils import StopWatch
from trapdata.ml.models.localization import (
    MothObjectDetector_FasterRCNN,
    GenericObjectDetector_FasterRCNN_MobileNet,
)
from trapdata.ml.models.classification import (
    MothNonMothClassifier,
    UKDenmarkMothSpeciesClassifier,
)
from trapdata.ml.models.tracking import find_all_tracks, summarize_tracks


# @newrelic.agent.background_task()
def test_tracking(db_path, image_base_directory, sample_size, skip_queue):

    # db_path = ":memory:"
    get_db(db_path, create=True)
    Session = get_session_class(db_path)

    get_or_create_monitoring_sessions(db_path, image_base_directory)

    clear_all_queues(db_path)

    with Session() as session:
        ms = session.execute(
            select(MonitoringSession)
            .order_by(MonitoringSession.num_images.desc())
            .limit(1)
        ).scalar()

        print("Using Monitoring Session:", ms)

        if not skip_queue:
            for image in ms.images:
                add_image_to_queue(db_path, image.id)

    if torch.cuda.is_available():
        object_detector = MothObjectDetector_FasterRCNN(db_path=db_path, batch_size=2)
    else:
        object_detector = GenericObjectDetector_FasterRCNN_MobileNet(
            db_path=db_path, batch_size=2
        )
    moth_nonmoth_classifier = MothNonMothClassifier(db_path=db_path, batch_size=300)
    species_classifier = UKDenmarkMothSpeciesClassifier(db_path=db_path, batch_size=300)

    check_db(db_path, quiet=False)

    object_detector.run()
    moth_nonmoth_classifier.run()
    species_classifier.run()

    logger.info("Classification complete")

    assert species_classifier.model is not None

    with Session() as session:
        find_all_tracks(
            monitoring_session=ms, cnn_model=species_classifier.model, session=session
        )

        summary = summarize_tracks(session=session)
        print(summary)


if __name__ == "__main__":
    image_base_directory = pathlib.Path(__file__).parent / "images/sequential"
    logger.info(f"Using test images from: {image_base_directory}")

    local_weights_path = torch.hub.get_dir()
    logger.info(f"Looking for or downloading weights in {local_weights_path}")
    os.environ["LOCAL_WEIGHTS_PATH"] = local_weights_path
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
        skip_queue = True
    else:
        db_filepath = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = f"sqlite+pysqlite:///{db_filepath.name}"
        skip_queue = False

    logger.info(f"Using temporary DB: {db_path}")

    with StopWatch() as t:
        test_tracking(
            db_path, image_base_directory, sample_size=10, skip_queue=skip_queue
        )
    logger.info(t)

    logger.info(f"Keeping temporary DB: {db_path}")