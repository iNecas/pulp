# -*- coding: utf-8 -*-
#
# Copyright © 2013 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the License
# (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied, including the
# implied warranties of MERCHANTABILITY, NON-INFRINGEMENT, or FITNESS FOR A
# PARTICULAR PURPOSE.
# You should have received a copy of GPLv2 along with this software;
# if not, see http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt

from celery import beat


class Scheduler(beat.Scheduler):
    max_interval = 60

    def setup_schedule(self):
        # load schedules from DB
        self._last_sync = 0
        return super(Scheduler, self).setup_schedule()

    def _changes(self):
        """
        :return:    True iff the collection of scheduled calls has changed.
        :rtype:     bool
        """
        # possibly add a timestamp to the schedule entries, and look for either
        # a change in the number of entries in the collection, or a timestamp
        # newer than what's currently loaded
        return True