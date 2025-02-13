from rest_framework import permissions

class IsAccountant(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.user_type == 'accountant'

class IsClientOrAccountant(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        if request.user.user_type == 'accountant':
            return obj.uploaded_by.accounting_firms.filter(owner=request.user).exists()
        return obj.uploaded_by == request.user 