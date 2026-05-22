# -*- coding: utf-8 -*-
#
# This file is part of SENAITE.CORE.
#
# SENAITE.CORE is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright 2018-2025 by it's authors.
# Some rights reserved, see README and LICENSE.

import six

from AccessControl import getSecurityManager
from bika.lims.api import get_portal
from bika.lims.api import get_tool
from bika.lims.api import get_current_user
from Products.CMFPlone.RegistrationTool import get_member_by_login_name
from Products.PlonePAS.tools.groupdata import GroupData
from Products.PlonePAS.tools.memberdata import MemberData
from bika.lims import logger
from Products.CMFCore.utils import getToolByName


def get_user(user=None):
    """Get the user object

    :param user: A user id, memberdata object or None for the current user
    :returns: Plone User (PlonePAS) / Propertied User (PluggableAuthService)
    """
    if user is None:
        # Return the current authenticated user
        user = getSecurityManager().getUser()
    elif isinstance(user, MemberData):
        # MemberData wrapped user -> get the user object
        user = user.getUser()
    elif isinstance(user, six.string_types):
        # User ID -> get the user
        user = get_member_by_login_name(get_portal(), user, False)
        if user:
            user = user.getUser()
    return user


def get_user_id(user=None):
    """Get the user id of the current authenticated user

    :param user: A user id, memberdata object or None for the current user
    :returns: Plone user ID
    """
    user = get_user(user)
    if user is None:
        return None
    return user.getId()


def get_group(group):
    """Return the group

    :param group: The group name/id
    :returns: Group
    """
    portal_groups = get_tool("portal_groups")
    if isinstance(group, six.string_types):
        group = portal_groups.getGroupById(group)
    elif isinstance(group, GroupData):
        group = group
    return group


def get_groups(user=None):
    """Return the groups of the user

    :param user: A user id, memberdata object or None for the current user
    :returns: List of groups
    """
    portal_groups = get_tool("portal_groups")
    user = get_user(user)
    if user is None:
        return []
    return portal_groups.getGroupsForPrincipal(user)


def add_group(group, user=None):
    """Add the user to the group
    """
    user = get_user(user)

    if user is None:
        raise ValueError("User '{}' not found".format(repr(user)))

    if isinstance(group, six.string_types):
        group = [group]
    elif isinstance(group, GroupData):
        group = [group]

    portal_groups = get_tool("portal_groups")
    for group in map(get_group, group):
        if group is None:
            continue
        portal_groups.addPrincipalToGroup(get_user_id(user), group.getId())

    return get_groups(user)


def del_group(group, user=None):
    """Remove the user to the group
    """
    user = get_user(user)

    if user is None:
        raise ValueError("User '{}' not found".format(repr(user)))

    if isinstance(group, six.string_types):
        group = [group]
    elif isinstance(group, GroupData):
        group = [group]

    portal_groups = get_tool("portal_groups")
    for group in map(get_group, group):
        if group is None:
            continue
        portal_groups.removePrincipalFromGroup(
            get_user_id(user), group.getId())

    return get_groups(user)

def get_current_contact(context, user_id=None):
    """Return LabContact bound to the given user_id (or current user if None).
    Strategy:
      1) Try portal_catalog (if indexed)
      2) Fallback: iterate bika_setup/bika_labcontacts container
    """
    # resolve user_id
    if not user_id:
        user = get_current_user()
        if not user:
            logger.warning("get_current_contact: no current user")
            return None
        user_id = user.getId()

    try:
        catalog = getToolByName(context, "portal_catalog")
        brains = catalog(portal_type="LabContact")
        for brain in brains:
            try:
                contact = brain.getObject()
                if hasattr(contact, "getUsername") and contact.getUsername() == user_id:
                    return contact
            except Exception as e:
                logger.warning("get_current_contact: brain.getObject failed: %s", e)
    except Exception as e:
        logger.warning("get_current_contact: portal_catalog error: %s", e)

    # 2) fallback: container iterate
    try:
        setup = getToolByName(context, "bika_setup")
        labcontacts = getattr(setup, "bika_labcontacts", None)
        if labcontacts:
            for obj in labcontacts.objectValues():
                try:
                    if hasattr(obj, "getUsername") and obj.getUsername() == user_id:
                        return obj
                except Exception:
                    continue
    except Exception as e:
        logger.warning("get_current_contact: fallback iterate contacts failed: %s", e)

    logger.warning("get_current_contact: no LabContact matched for user '%s'", user_id)
    return None

def get_allowed_keywords(context, admin_all_marker="__ALL__"):
    """Return allowed AnalysisService keywords for current user.
    - admin  → return admin_all_marker (e.g. "__ALL__") to indicate allow all
    - normal → return list of keywords from LabContact.AllowedServices
    - no contact / no services → return []
    """
    user = get_current_user()
    if not user:
        logger.warning("get_allowed_keywords: no current user")
        return []

    user_id = user.getId()

    # admin shortcut
    if user_id == "admin":
        return admin_all_marker

    # find contact
    contact = get_current_contact(context, user_id=user_id)
    if not contact:
        logger.warning("get_allowed_keywords: no LabContact for user '%s'", user_id)
        return []

    # extract keywords
    try:
        services = contact.getAllowedServices() or []
    except Exception as e:
        logger.warning("get_allowed_keywords: read AllowedServices failed: %s", e)
        return []

    keywords = []
    for svc in services:
        if not svc:
            continue
        try:
            kw = svc.getKeyword() if hasattr(svc, "getKeyword") else str(svc)
            keywords.append(kw)
        except Exception as e:
            logger.warning("get_allowed_keywords: extract keyword failed: %s", e)

    return keywords