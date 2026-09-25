"""Durable SQLAlchemy LangGraph checkpoint saver; no pickle and no API exposure."""

from langgraph.checkpoint.base import WRITES_IDX_MAP, BaseCheckpointSaver, CheckpointTuple
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.rag.models import CheckpointRecord, CheckpointWrite


class DatabaseSaver(BaseCheckpointSaver):
    def __init__(self, session_factory):
        super().__init__()
        self.sessions = session_factory

    def get_tuple(self, config):
        c = config["configurable"]
        with self.sessions() as db:
            stmt = select(CheckpointRecord).where(
                CheckpointRecord.run_id == c["thread_id"],
                CheckpointRecord.namespace == c.get("checkpoint_ns", ""),
            )
            if c.get("checkpoint_id"):
                stmt = stmt.where(CheckpointRecord.checkpoint_id == c["checkpoint_id"])
            row = db.scalar(stmt.order_by(CheckpointRecord.checkpoint_id.desc()))
            if row is None:
                return None
            key = {
                "thread_id": row.run_id,
                "checkpoint_ns": row.namespace,
                "checkpoint_id": row.checkpoint_id,
            }
            writes = db.scalars(
                select(CheckpointWrite)
                .where(
                    CheckpointWrite.run_id == row.run_id,
                    CheckpointWrite.namespace == row.namespace,
                    CheckpointWrite.checkpoint_id == row.checkpoint_id,
                )
                .order_by(CheckpointWrite.task_id, CheckpointWrite.write_index)
            ).all()
            return CheckpointTuple(
                config={"configurable": key},
                checkpoint=self.serde.loads_typed((row.encoding, row.payload)),
                metadata=row.meta,
                parent_config={"configurable": {**key, "checkpoint_id": row.parent_id}}
                if row.parent_id
                else None,
                pending_writes=[
                    (w.task_id, w.channel, self.serde.loads_typed((w.encoding, w.payload)))
                    for w in writes
                ],
            )

    def put(self, config, checkpoint, metadata, new_versions):
        c = config["configurable"]
        encoding, payload = self.serde.dumps_typed(checkpoint)
        with self.sessions() as db:
            db.add(
                CheckpointRecord(
                    run_id=c["thread_id"],
                    namespace=c.get("checkpoint_ns", ""),
                    checkpoint_id=checkpoint["id"],
                    parent_id=c.get("checkpoint_id"),
                    encoding=encoding,
                    payload=payload,
                    meta=metadata,
                )
            )
            db.commit()
        return {"configurable": {**c, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        c = config["configurable"]
        for index, (channel, value) in enumerate(writes):
            key = (
                c["thread_id"],
                c.get("checkpoint_ns", ""),
                c["checkpoint_id"],
                task_id,
                WRITES_IDX_MAP.get(channel, index),
            )
            encoding, payload = self.serde.dumps_typed(value)
            with self.sessions() as db:
                row = db.get(CheckpointWrite, key)
                if row:
                    if key[-1] < 0:
                        row.channel, row.encoding, row.payload = channel, encoding, payload
                else:
                    db.add(
                        CheckpointWrite(
                            run_id=key[0],
                            namespace=key[1],
                            checkpoint_id=key[2],
                            task_id=key[3],
                            write_index=key[4],
                            channel=channel,
                            encoding=encoding,
                            payload=payload,
                        )
                    )
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()

    def list(self, config, *, filter=None, before=None, limit=None):
        with self.sessions() as db:
            stmt = select(CheckpointRecord).where(
                CheckpointRecord.run_id == config["configurable"]["thread_id"]
            )
            rows = db.scalars(stmt.order_by(CheckpointRecord.checkpoint_id.desc())).all()
            for row in rows[:limit]:
                yield self.get_tuple(
                    {
                        "configurable": {
                            "thread_id": row.run_id,
                            "checkpoint_ns": row.namespace,
                            "checkpoint_id": row.checkpoint_id,
                        }
                    }
                )
