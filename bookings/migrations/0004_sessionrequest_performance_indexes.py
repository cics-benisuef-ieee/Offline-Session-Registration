from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bookings", "0003_exclusion_constraint"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="sessionrequest",
            index=models.Index(
                fields=["-created_at"],
                name="sreq_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="sessionrequest",
            index=models.Index(
                fields=["status", "-created_at"],
                name="sreq_status_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="sessionrequest",
            index=models.Index(
                fields=["requesting_unit", "-created_at"],
                name="sreq_unit_created_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="sessionrequest",
            index=models.Index(
                fields=["status", "start_at"],
                name="sreq_status_start_idx",
            ),
        ),
    ]
