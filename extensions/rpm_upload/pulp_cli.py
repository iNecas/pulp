# -*- coding: utf-8 -*-
#
# Copyright © 2012 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public
# License as published by the Free Software Foundation; either version
# 2 of the License (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied,
# including the implied warranties of MERCHANTABILITY,
# NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
# have received a copy of GPLv2 along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

from gettext import gettext as _
import hashlib
import os
import rpm

from okaara.prompt import COLOR_GREEN, COLOR_YELLOW

from   pulp.gc_client.framework.extensions import PulpCliCommand
import pulp.gc_client.util.upload as upload_lib

# -- constants ----------------------------------------------------------------

TYPE_RPM = 'rpm'

RPMTAG_NOSOURCE = 1051

COLOR_RUNNING = COLOR_GREEN
COLOR_PAUSED = COLOR_YELLOW

# -- framework hook -----------------------------------------------------------

def initialize(context):
    repo_section = context.cli.find_section('repo')
    uploads_section = repo_section.create_subsection('uploads', _('package and errata upload'))

    d = 'lists in progress and paused uploads'
    uploads_section.add_command(ListCommand(context, 'list', _(d)))

    d = 'uploads one or more RPMs into a repository'
    uploads_section.add_command(CreateCommand(context, 'rpm', _(d)))

    d = 'resumes a paused upload request'
    uploads_section.add_command(ResumeCommand(context, 'resume', _(d)))

    d = 'cancels an outstanding upload request'
    uploads_section.add_command(CancelCommand(context, 'cancel', _(d)))

# -- commands -----------------------------------------------------------------

class CreateCommand(PulpCliCommand):
    def __init__(self, context, name, description):
        PulpCliCommand.__init__(self, name, description, self.create)
        self.context = context
        self.prompt = context.prompt

        self.create_option('--repo-id', _('identifies the repository the packages will be uploaded into'), required=True)

        d = 'full path to the package to upload; may be specified multiple times ' \
            'for multiple files'
        self.create_option('--file', _(d), aliases=['-f'], allow_multiple=True, required=False)

        d = 'full path to a directory containing RPMs, all of which will be uploaded; ' \
            'may be specified multiple times for multiple directories'
        self.create_option('--dir', _(d), aliases=['-d'], allow_multiple=True, required=False)

    def create(self, **kwargs):

        repo_id = kwargs['repo-id']

        # Resolve the total list of RPMs to upload
        all_rpm_filenames = kwargs['file'] or []

        for d in kwargs['dir'] or []:
            # Sanity check
            if not os.path.exists(d):
                self.context.prompt.render_failure_message(_('Directory %(d)s does not exist') % {'d' : d})
                return os.EX_IOERR

            # Find all RPMs
            dir_rpms = [f for f in os.listdir(d) if f.endswith('.rpm')]
            for f in dir_rpms:
                full_filename = os.path.join(d, f)
                all_rpm_filenames.append(full_filename)

        # Make sure at least one RPM was found
        if len(all_rpm_filenames) is 0:
            self.context.prompt.render_failure_message(_('No RPMs selected for upload'))
            return os.EX_DATAERR

        # Integrity check on the total list of RPM files
        for f in all_rpm_filenames:
            if not os.path.exists(f) or not os.access(f, os.R_OK):
                self.context.prompt.render_failure_message(_('File %(f)s does not exist or could not be read') % {'f' : f})
                return os.EX_IOERR
            if not os.path.isfile(f):
                self.context.prompt.render_failure_message(_('%(f)s is not a file') % {'f' : f})
                return os.EX_IOERR

        # Extract the required metadata for each RPM
        self.prompt.write(_('Extracting necessary metdata for each RPM...'))
        bar = self.prompt.create_progress_bar()

        rpm_tuples = []
        for i, f in enumerate(all_rpm_filenames):
            bar.render(i+1, len(all_rpm_filenames), message=_('Analyzing: %(n)s') % {'n' : os.path.basename(f)})
            unit_key, metadata = _generate_rpm_data(f)
            rpm_tuples.append( (f, unit_key, metadata))

        self.prompt.write(_('... completed'))
        self.prompt.render_spacer()

        # Initialize all uploads
        upload_manager = _upload_manager(self.context)

        self.prompt.write(_('Creating upload requests on the server...'))
        bar = self.prompt.create_progress_bar()

        upload_ids = []
        for i, job in enumerate(rpm_tuples):
            bar.render(i+1, len(rpm_tuples), message=_('Initializing: %(n)s') % {'n' : os.path.basename(job[0])})
            upload_id = upload_manager.initialize_upload(job[0], repo_id, TYPE_RPM, job[1], job[2])
            upload_ids.append(upload_id)

        self.prompt.write(_('... completed'))
        self.prompt.render_spacer()

        # Start the upload process
        _perform_upload(self.context, upload_manager, upload_ids)

class ResumeCommand(PulpCliCommand):

    def __init__(self, context, name, description):
        PulpCliCommand.__init__(self, name, description, self.resume)
        self.context = context

    def resume(self, **kwargs):
        self.context.prompt.render_title(_('Upload Requests'))

        upload_manager = _upload_manager(self.context)

        uploads = upload_manager.list_uploads()

        if len(uploads) is 0:
            self.context.prompt.render_paragraph('No outstanding uploads found')
            return

        non_running_uploads = [u for u in uploads if not u.is_running]
        if len(non_running_uploads) is 0:
            d = 'All requests are currently in the process of being uploaded.'
            self.context.prompt.render_paragraph(_(d))
            return

        source_filenames = [os.path.basename(u.source_filename) for u in non_running_uploads]
        q = _('Select one or more uploads to resume: ')
        selected_indexes = self.context.prompt.prompt_multiselect_menu(q, source_filenames, interruptable=True)

        if selected_indexes is self.context.prompt.ABORT or len(selected_indexes) is 0:
            return

        selected_uploads = [u for i, u in enumerate(non_running_uploads) if i in selected_indexes]
        selected_filenames = [os.path.basename(u.source_filename) for u in selected_uploads]
        selected_ids = [u.upload_id for u in selected_uploads]

        self.context.prompt.render_paragraph(_('Resuming upload for: %(u)s') % {'u' : ', '.join(selected_filenames)})

        _perform_upload(self.context, upload_manager, selected_ids)

class ListCommand(PulpCliCommand):

    def __init__(self, context, name, description):
        PulpCliCommand.__init__(self, name, description, self.list)
        self.context = context

    def list(self, **kwargs):
        self.context.prompt.render_title(_('Upload Requests'))

        upload_manager = _upload_manager(self.context)
        uploads = upload_manager.list_uploads()

        if len(uploads) is 0:
            self.context.prompt.render_paragraph(_('No outstanding uploads found'))
            return

        for upload in uploads:
            if upload.is_running:
                state = '[%-7s]' % self.context.prompt.color(_('Running'), COLOR_RUNNING)
            else:
                state = '[%-7s]' % self.context.prompt.color(_('Paused'), COLOR_PAUSED)

            template = '%s %s'
            message = template % (state, os.path.basename(upload.source_filename))
            self.context.prompt.write(message)

        self.context.prompt.render_spacer()

class CancelCommand(PulpCliCommand):

    def __init__(self, context, name, description):
        PulpCliCommand.__init__(self, name, description, self.cancel)
        self.context = context

        d = 'removes the client-side tracking file for the upload regardless of ' \
        'whether or not it was able to be deleted on the server; this should ' \
        'only be used in the event that the server\'s knowledge of an upload ' \
        'has been removed'
        self.create_flag('--force', _(d))

    def cancel(self, **kwargs):
        force = kwargs['force'] or False

        self.context.prompt.render_title(_('Upload Requests'))

        upload_manager = _upload_manager(self.context)
        uploads = upload_manager.list_uploads()

        # Punch out early if there are no requests we can act on
        if len(uploads) is 0:
            self.context.prompt.render_paragraph(_('No outstanding uploads found'))
            return

        non_running_uploads = [u for u in uploads if not u.is_running]
        if len(non_running_uploads) is 0:
            d = 'All requests are currently in the process of being uploaded. ' \
            'Only paused uploads may be cancelled.'
            self.context.prompt.render_paragraph(_(d))
            return

        # Prompt for which upload requests to cancel
        source_filenames = [os.path.basename(u.source_filename) for u in non_running_uploads]
        q = _('Select one or more uploads to cancel: ')
        selected_indexes = self.context.prompt.prompt_multiselect_menu(q, source_filenames, interruptable=True)

        if selected_indexes is self.context.prompt.ABORT or len(selected_indexes) is 0:
            return

        selected_uploads = [u for i, u in enumerate(non_running_uploads) if i in selected_indexes]
        selected_filenames = [os.path.basename(u.source_filename) for u in selected_uploads]
        selected_ids = [u.upload_id for u in selected_uploads]

        error_encountered = False
        for i, upload_id in enumerate(selected_ids):
            self.context.prompt.write(_('Deleting: %(f)s') % {'f' : selected_filenames[i]})
            try:
                upload_manager.delete_upload(upload_id, force=force)
            except Exception, e:
                self.context.exception_handler.handle_exception(e)
                error_encountered = True

        if error_encountered:
            return os.EX_IOERR
        else:
            return os.EX_OK

# -- utility ------------------------------------------------------------------

def _upload_manager(context):
    """
    Instantiates and configures the upload manager.

    @return: initialized and ready to run upload manager instance
    @rtype:  UploadManager
    """
    upload_working_dir = context.client_config.get('filesystem', 'upload_working_dir')
    upload_working_dir = os.path.expanduser(upload_working_dir)
    chunk_size = int(context.client_config.get('server', 'upload_chunk_size'))
    upload_manager = upload_lib.UploadManager(upload_working_dir, context.server, chunk_size)
    upload_manager.initialize()
    return upload_manager

def _perform_upload(context, upload_manager, upload_ids):
    """
    Uploads (resumes if necessary) uploading the given upload requests. The
    context is used to retrieve the bindings and this call will use the prompt
    to display output to the screen.

    @param context: framework provided context
    @type  context: PulpCliContext

    @param upload_manager: initialized upload manager instance
    @type  upload_manager: UploadManager

    @param upload_ids: list of upload IDs to handle
    @type  upload_ids: list
    """

    d = 'Starting upload of selected packages. If this process is stopped through '\
        'ctrl+c, the uploads will be paused and may be resumed later using the '\
        'resume command or cancelled entirely using the cancel command.'
    context.prompt.render_paragraph(_(d))

    # Upload and import each upload. The try block is inside of the loop to
    # allow uploads to continue even if one hits an exception. The exception
    # handler is called directly to use the standard logging/display for
    # exceptions but otherwise the next upload is allowed. The only variation
    # is that a KeyboardInterrupt represents pausing the upload process.
    for upload_id in upload_ids:
        try:
            tracker = upload_manager.get_upload(upload_id)

            # Upload the bits
            context.prompt.write(_('Uploading: %(n)s') % {'n' : os.path.basename(tracker.source_filename)})
            bar = context.prompt.create_progress_bar()

            upload_manager.upload(upload_id, bar.render)

            context.prompt.write(_('... completed'))
            context.prompt.render_spacer()

            # Import the upload request
            context.prompt.write(_('Importing into the repository...'))
            upload_manager.import_upload(upload_id)
            context.prompt.write(_('... completed'))
            context.prompt.render_spacer()

            # Delete the request
            context.prompt.write(_('Deleting the upload request...'))
            upload_manager.delete_upload(upload_id)
            context.prompt.write(_('... completed'))
            context.prompt.render_spacer()

        except KeyboardInterrupt:
            d = 'Uploading paused'
            context.prompt.render_paragraph(_(d))
            return

        except Exception, e:
            context.exception_handler.handle_exception(e)

def _generate_rpm_data(rpm_filename):

    # Expected metadata fields:
    # "vendor", "description", "buildhost", "license", "vendor", "requires", "provides", "relativepath", "filename"
    #
    # Expected unit key fields:
    # "name", "epoch", "version", "release", "arch", "checksumtype", "checksum"

    unit_key = dict()
    metadata = dict()

    # Read the RPM header attributes for use later
    ts = rpm.TransactionSet()
    ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)
    fd = os.open(rpm_filename, os.O_RDONLY)
    headers = ts.hdrFromFdno(fd)
    os.close(fd)

    # -- Unit Key -----------------------

    # Checksum
    unit_key['checksumtype'] = 'sha256' # hardcoded to this in v1 so leaving this way for now

    m = hashlib.new(unit_key['checksumtype'])
    f = open(rpm_filename, 'r')
    while 1:
        buffer = f.read(65536)
        if not buffer:
            break
        m.update(buffer)
    f.close()

    unit_key['checksum'] = m.hexdigest()

    # Name, Version, Release, Epoch
    for k in ['name', 'version', 'release', 'epoch']:
        unit_key[k] = headers[k]

    #   Epoch munging
    if unit_key['epoch'] is None:
        unit_key['epoch'] = str(0)
    else:
        unit_key['epoch'] = str(unit_key['epoch'])

    # Arch
    if headers['sourcepackage']:
        if RPMTAG_NOSOURCE in headers.keys():
            unit_key['arch'] = 'nosrc'
        else:
            unit_key['arch'] = 'src'
    else:
        unit_key['arch'] = headers['arch']

    # -- Unit Metadata ------------------

    metadata['relativepath'] = os.path.basename(rpm_filename)
    metadata['filename'] = os.path.basename(rpm_filename)

    metadata['requires'] = [(r,) for r in headers['requires']]
    metadata['provides'] = [(p,) for p in headers['provides']]

    metadata['buildhost'] = headers['buildhost']
    metadata['license'] = headers['license']
    metadata['vendor'] = headers['vendor']
    metadata['description'] = headers['description']

    return unit_key, metadata
