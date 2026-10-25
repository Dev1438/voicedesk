from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("complaints", "0003_complaint_possible_duplicate_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="complaint",
            name="denoising_status",
            field=models.CharField(
                choices=[
                    ("not_attempted", "Not Attempted"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                ],
                default="not_attempted",
                max_length=16,
            ),
        ),
    ]
