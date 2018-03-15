# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (c) 2013 E-MIPS (http://www.e-mips.com.ar) All Rights Reserved.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General
#    Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

from openerp.osv import osv, fields
from openerp.tools.translate import _
import base64
from tempfile import TemporaryFile


class wsaa_load_config(osv.osv_memory):
    _name = 'wsaa.load.config'

    _columns = {
        'certificate': fields.binary('Certificate of Approval',
                                     help="You certificate (.crt)",
                                     filter="*.crt"),
        'cert_name': fields.char('Cert FileName'),
        'key': fields.binary('Private Key',
                             help="You Privary Key Here",
                             filter="*.key"),
        'key_name': fields.char('Key FileName'),
    }

    def read_file(self, cr, uid, ids, filename=False,
                  filedata=False, ext=False, context={}):
        if not filename or not filedata or not ext:
            raise Exception('Wrong call parameters to `load_file` method')
        if not (filedata):
            raise osv.except_osv(_('Error'),
                                 _('You must enter a File'))
        pieces = filename.split('.')
        if len(pieces) < 2 or pieces[-1] != ext:
            raise osv.except_osv(_('Error'),
                                 _('The Filename should end in ".%s"' % ext))
        fileobj = TemporaryFile('w+')
        fileobj.write(base64.decodestring(filedata))
        fileobj.seek(0)
        lines = fileobj.read()
        fileobj.close()
        return lines

    def load_cert(self, cr, uid, ids, context={}):
        form_id = context.get('active_ids', False)
        if not form_id or len(form_id) != 1:
            raise Exception('Wizard method call without `active_ids` in ctx')
        wiz = self.browse(cr, uid, ids, context)
        certificate = wiz.certificate
        cert_name = wiz.cert_name
        filedata = self.read_file(cr, uid, ids, cert_name,
                                  certificate, 'crt', context)
        wsaa_config_obj = self.pool['wsaa.config']
        write_vals = {
            'certificate': filedata,
        }
        wsaa_config_obj.write(cr, uid, form_id, write_vals, context)

    def load_key(self, cr, uid, ids, context={}):
        form_id = context.get('active_ids', False)
        if not form_id or len(form_id) != 1:
            raise Exception('Wizard method call without `active_ids` in ctx')
        wiz = self.browse(cr, uid, ids, context)
        key = wiz.key
        key_name = wiz.key_name
        filedata = self.read_file(cr, uid, ids, key_name,
                                  key, 'key', context)
        wsaa_config_obj = self.pool['wsaa.config']
        write_vals = {
            'key': filedata,
        }
        wsaa_config_obj.write(cr, uid, form_id, write_vals, context)
