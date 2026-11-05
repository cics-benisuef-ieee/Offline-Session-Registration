from django.db import migrations


def create_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist;")
    schema_editor.execute(
        """
        ALTER TABLE bookings_sessionrequest
        ADD CONSTRAINT session_request_no_approved_overlap
        EXCLUDE USING gist (
            place_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (status = 'approved');
        """
    )


def drop_exclusion_constraint(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    schema_editor.execute(
        """
        ALTER TABLE bookings_sessionrequest
        DROP CONSTRAINT IF EXISTS session_request_no_approved_overlap;
        """
    )


class Migration(migrations.Migration):
    dependencies = [
        ("bookings", "0002_sessionrequest_sreq_conflict_idx"),
    ]

    operations = [
        migrations.RunPython(create_exclusion_constraint, drop_exclusion_constraint),
    ]
