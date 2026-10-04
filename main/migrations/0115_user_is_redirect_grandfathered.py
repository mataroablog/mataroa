from django.db import migrations, models


def grandfather_existing_users(apps, schema_editor):
    User = apps.get_model("main", "User")
    User.objects.using(schema_editor.connection.alias).update(
        is_redirect_grandfathered=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0114_user_is_delisted"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="is_redirect_grandfathered",
            field=models.BooleanField(
                default=False,
                help_text="Keep redirect access for accounts created before premium was required.",
            ),
        ),
        migrations.RunPython(
            grandfather_existing_users, reverse_code=migrations.RunPython.noop
        ),
    ]
