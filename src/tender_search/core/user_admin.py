from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, password_validation, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods


def can_manage_users(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


class CreateUserForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = get_user_model()
        fields = ('username', 'first_name', 'last_name', 'email')
        labels = {'username': 'Логин', 'first_name': 'Имя', 'last_name': 'Фамилия',
                  'email': 'E-mail'}

    def clean_username(self):
        username = self.cleaned_data['username']
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Пользователь с таким логином уже существует.')
        return username


class EditUserForm(forms.ModelForm):
    role = forms.ChoiceField(label='Роль', choices=[
        ('user', 'Пользователь'), ('admin', 'Администратор')],
        help_text='Администратор управляет пользователями и видит состояние базы закупок.')
    password1 = forms.CharField(label='Новый пароль', required=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
                               help_text='Оставьте пустым, чтобы сохранить текущий пароль.')
    password2 = forms.CharField(label='Повторите новый пароль', required=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    class Meta(CreateUserForm.Meta):
        fields = (*CreateUserForm.Meta.fields, 'is_active')
        labels = {**CreateUserForm.Meta.labels, 'is_active': 'Доступ включён'}
        help_texts = {'is_active': 'Отключённый пользователь не сможет войти в приложение.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].initial = 'admin' if self.instance.is_superuser else 'user'
        if self.instance.username == 'admin':
            self.fields['username'].disabled = True
            self.fields['is_active'].disabled = True
            self.fields['role'].disabled = True

    def clean_username(self):
        username = self.cleaned_data['username']
        if get_user_model().objects.filter(username__iexact=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Пользователь с таким логином уже существует.')
        return username

    def clean(self):
        data = super().clean()
        if data.get('password1') != data.get('password2'):
            self.add_error('password2', 'Пароли не совпадают.')
        return data

    def _post_clean(self):
        super()._post_clean()
        if password := self.cleaned_data.get('password1'):
            try:
                password_validation.validate_password(password, self.instance)
            except ValidationError as error:
                self.add_error('password1', error)

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_superuser = self.cleaned_data['role'] == 'admin'
        user.is_staff = user.is_superuser
        if password := self.cleaned_data.get('password1'):
            user.set_password(password)
        if commit:
            user.save()
        return user


class DeleteUserForm(forms.Form):
    confirm = forms.BooleanField(label='Подтверждаю удаление пользователя и его личных данных')


@sensitive_post_parameters('password1', 'password2')
@login_required
@require_http_methods(['GET', 'POST'])
def edit_user(request, user_id):
    if not can_manage_users(request.user):
        raise PermissionDenied
    account = get_object_or_404(get_user_model(), pk=user_id)
    form = EditUserForm(request.POST if request.method == 'POST' else None, instance=account)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                account = form.save()
        except IntegrityError:
            form.add_error('username', 'Пользователь с таким логином уже существует.')
        else:
            if account.pk == request.user.pk:
                update_session_auth_hash(request, account)
            messages.success(request, f'Пользователь «{account.username}» изменён.')
            return redirect('administration_users')
    return render(request, 'core/user_edit.html', {'form': form, 'account': account})


@login_required
@require_http_methods(['GET', 'POST'])
def delete_user(request, user_id):
    if not can_manage_users(request.user):
        raise PermissionDenied
    account = get_object_or_404(get_user_model(), pk=user_id)
    if account.pk == request.user.pk or account.username == 'admin':
        raise PermissionDenied
    form = DeleteUserForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        username = account.username
        with transaction.atomic():
            # Remove watches before Favorite deletion signals can enqueue a stop notice.
            from .models import AnnouncementWatch
            AnnouncementWatch.objects.filter(owner=account).delete()
            account.delete()
        messages.success(request, f'Пользователь «{username}» удалён.')
        return redirect('administration_users')
    return render(request, 'core/user_delete.html', {'form': form, 'account': account})


@sensitive_post_parameters('password1', 'password2')
@login_required
@require_http_methods(['GET', 'POST'])
def users(request):
    if not can_manage_users(request.user):
        raise PermissionDenied
    form = CreateUserForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save(commit=False)
                user.is_staff = user.is_superuser = False
                user.is_active = True
                user.save()
        except IntegrityError:
            form.add_error('username', 'Пользователь с таким логином уже существует.')
        else:
            messages.success(request, f'Пользователь «{user.username}» добавлен.')
            return redirect('administration_users')
    return render(request, 'core/users.html', {
        'form': form, 'users': get_user_model().objects.order_by('username'),
    })
