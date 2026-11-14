-- DELETE for the replica writer on the two tables its delete events touch (kafka-replication):
-- usuario.eliminado removes the patient profile of the account; cita.eliminada removes the
-- appointment. Only these two privileges are added. Nothing else is granted or revoked.
-- This revises the note in 0008 that neither role may delete an appointment: the writer now
-- deletes an appointment only when its SQL Server row is deleted (the demo reset). The reader
-- keeps read-only access. Idempotent: granting a privilege that is already held changes nothing.

GRANT DELETE ON replica.pacientes, replica.citas TO replica_writer;
