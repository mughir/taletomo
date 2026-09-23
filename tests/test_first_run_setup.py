import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse


User = get_user_model()


@pytest.mark.django_db
def test_empty_install_login_offers_first_account_setup(client):
    response = client.get(reverse("login"))

    assert response.status_code == 200
    page = response.content.decode()
    assert "New to this installation?" in page
    assert "Create your account" in page
    assert reverse("taletomo:first_user_setup") in page


@pytest.mark.django_db
def test_first_account_setup_creates_user_and_signs_them_in(client):
    response = client.post(
        reverse("taletomo:first_user_setup"),
        {
            "username": "writer_one",
            "password1": "Novel!Safe-Account-7294",
            "password2": "Novel!Safe-Account-7294",
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("taletomo:home")
    user = User.objects.get(username="writer_one")
    assert user.is_active
    assert client.get(reverse("taletomo:home")).status_code == 200


@pytest.mark.django_db
def test_first_account_setup_is_locked_after_an_account_exists(client):
    User.objects.create_user(username="already_here", password="Existing!Password-9292")

    response = client.get(reverse("taletomo:first_user_setup"))

    assert response.status_code == 302
    assert response.url == reverse("login")
    login_response = client.get(reverse("login"))
    assert "First time here? Create your account" not in login_response.content.decode()


@pytest.mark.django_db
def test_invalid_first_account_form_shows_actionable_errors(client):
    response = client.post(
        reverse("taletomo:first_user_setup"),
        {"username": "writer_two", "password1": "short", "password2": "different"},
    )

    assert response.status_code == 200
    assert not User.objects.filter(username="writer_two").exists()
    assert "The two password fields didn’t match" in response.content.decode() or "The two password fields didn't match" in response.content.decode()


@pytest.mark.django_db
def test_first_account_setup_cannot_create_another_user_after_cli_user_exists(client):
    User.objects.create_user(username="cli_admin", password="Existing!Password-9292")

    response = client.post(
        reverse("taletomo:first_user_setup"),
        {
            "username": "second_writer",
            "password1": "Novel!Safe-Account-7294",
            "password2": "Novel!Safe-Account-7294",
        },
    )

    assert response.status_code == 302
    assert not User.objects.filter(username="second_writer").exists()
