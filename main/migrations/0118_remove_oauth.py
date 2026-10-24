from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0117_oauth_admin_names"),
    ]

    # Keep the applied OAuth migrations in history and drop dependent tables first.
    # Reversing this migration recreates empty tables; OAuth credentials are lost.
    operations = [
        migrations.DeleteModel(name="OAuthToken"),
        migrations.DeleteModel(name="OAuthGrant"),
        migrations.DeleteModel(name="OAuthClient"),
    ]
