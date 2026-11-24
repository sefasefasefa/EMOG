from django.db import migrations, models


def enable_whoches_background(apps, schema_editor):
    User = apps.get_model("agent_app", "User")
    User.objects.filter(username__iexact="whoches").update(continue_in_background=True)


class Migration(migrations.Migration):

    dependencies = [
        ("agent_app", "0008_buildplanitem_buildrun_researchdossier_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="continue_in_background",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(enable_whoches_background, migrations.RunPython.noop),
    ]
