from medicalbooking.modules.synchronization.infrastructure.kafka_handler import KafkaOutboxHandler
from medicalbooking.workers.outbox_dispatcher import build_handlers


def test_the_kafka_destination_is_wired_only_when_a_broker_is_configured():
    handlers = build_handlers(engine=object(), kafka_bootstrap=None, kafka_topic="t")

    assert not any(isinstance(h, KafkaOutboxHandler) for h in handlers)


def test_a_configured_broker_adds_the_kafka_destination(monkeypatch):
    monkeypatch.setattr(
        "medicalbooking.workers.outbox_dispatcher.build_kafka_producer",
        lambda bootstrap: object(),
    )

    handlers = build_handlers(
        engine=object(), kafka_bootstrap="127.0.0.1:9092", kafka_topic="medicalbooking.events"
    )

    kafka = [h for h in handlers if isinstance(h, KafkaOutboxHandler)]
    assert len(kafka) == 1
    assert kafka[0].destination_code == "KAFKA"


def test_the_mongo_destination_is_wired_only_with_a_projection_database():
    from pymongo import MongoClient

    from medicalbooking.modules.replica.infrastructure.mongo_projection import MongoOutboxHandler

    without = build_handlers(engine=object(), kafka_bootstrap=None, kafka_topic="t")
    assert not any(isinstance(h, MongoOutboxHandler) for h in without)

    database = MongoClient("mongodb://127.0.0.1:1", connect=False)["medilo_test"]
    with_mongo = build_handlers(
        engine=object(), kafka_bootstrap=None, kafka_topic="t", mongo_database=database
    )
    mongo = [h for h in with_mongo if isinstance(h, MongoOutboxHandler)]
    assert len(mongo) == 1
    assert mongo[0].destination_code == "MONGODB"
