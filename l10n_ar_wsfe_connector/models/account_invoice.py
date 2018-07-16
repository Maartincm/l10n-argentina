# -*- coding: utf-8 -*-
###############################################################################
#   Copyright (c) 2018 Eynes/E-MIPS (Martín Nicolás Cuesta)
#   License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).
###############################################################################

import logging

import ast
from lxml import etree

from openerp import api, fields, models, _
from openerp.exceptions import MissingError

from openerp.addons.connector.queue.job import job
# from openerp.addons.connector.session import ConnectorSession

from openerp.addons.web_auto_refresh.models.web_auto_refresh import \
        auto_refresh_kanban_list


_logger = logging.getLogger(__name__)


@job(default_channel='root.general')
def register_electronic_invoice(session, model_name, record_id):
    self = session.env[model_name].browse(record_id)
    _logger.info(_('Sending Invoice `%s` To Afip') % self.internal_number)
    try:
        self.action_aut_cae()
        self.signal_workflow('approved_invoice')
    except Exception as e:
        self.env.cr.rollback()
        if self.move_id:
            try:
                move = self.move_id
                self.move_id.state = 'draft'
                self.move_id = False
                move.unlink()
            except MissingError:
                pass
        self.signal_workflow('rejected_invoice')
        auto_refresh_kanban_list(self, 'connector', self.ids)
        self.env.cr.commit()
        raise e


@job(default_channel='root.invoice')
def validate_invoice(session, model_name, record_id):
    self = session.env[model_name].browse(record_id)
    try:
        _logger.info(_('User `%s` Begun an Invoice Validation for the ID: %s')
                     % (self.env.user.name, record_id))
        self.action_date_assign()
        self.action_move_create()
        self.action_number()
        self.queue('register_electronic_invoice', session=session)
    except Exception as e:
        self.env.cr.rollback()
        if self.move_id:
            try:
                move = self.move_id
                self.move_id.state = 'draft'
                self.move_id = False
                move.unlink()
            except MissingError:
                pass
        self.signal_workflow('error_invoice')
        self.write({
            'state': 'draft',
            'internal_number': False
        })
        self.env.cr.commit()
        auto_refresh_kanban_list(self, 'connector', self.ids)
        raise e


class AccountInvoice(models.Model):
    _name = 'account.invoice'
    _inherit = 'account.invoice'

    state = fields.Selection([
            ('draft', _('Draft')),
            ('proforma', _('Pro-forma')),
            ('proforma2', _('Pro-forma')),
            ('waiting', _('Waiting')),
            ('open', _('Open')),
            ('error', _('Error')),
            ('rejected', _('Rejected')),
            ('paid', _('Paid')),
            ('cancel', _('Cancelled')),
        ], string='Status', index=True, readonly=True, default='draft',
        track_visibility='onchange', copy=False,
        help=" * The 'Draft' status is used when a user is encoding a new and unconfirmed Invoice.\n"  # noqa
             " * The 'Pro-forma' when invoice is in Pro-forma status,invoice does not have an invoice number.\n"  # noqa
             " * The 'Open' status is used when user create invoice,a invoice number is generated.Its in open status till user does not pay invoice.\n"  # noqa
             " * The 'Paid' status is set automatically when the invoice is paid. Its related journal entries may or may not be reconciled.\n"  # noqa
             " * The 'Cancelled' status is used when user cancel invoice.")

    def _setup_base(self, *args):
        super(AccountInvoice, self)._setup_base(*args)
        for name, f in self._fields.items():
            if f.states and 'draft' in f.states:
                f.states['rejected'] = f.states['draft']

    def fields_view_get(self, cr, uid, view_id=None, view_type=None,
                        context=None, toolbar=False, submenu=False):
        res = super(AccountInvoice, self).fields_view_get(
            cr, uid, view_id=view_id, view_type=view_type, context=context,
            toolbar=toolbar, submenu=submenu)
        if view_type != 'form':
            return res
        doc = etree.XML(res['arch'])
        for node in doc.xpath('//field|//button|//group|//page'):
            states = node.get('states')
            attrs = node.get('attrs')
            modifiers = node.get('modifiers')
            if modifiers:
                modifiers = modifiers.replace('true', 'True')
                modifiers = modifiers.replace('false', 'False')
            if states and 'draft' in states:
                node.set('states', states + ',rejected')
            if (attrs and 'draft' in attrs) or \
                    (modifiers and 'draft' in modifiers):
                mod_dict = ast.literal_eval(attrs or modifiers)
                for attr, val in mod_dict.items():
                    new_val = []
                    if isinstance(val, (bool, str)):
                        continue
                    for i, triplet in enumerate(val):
                        if triplet[0] == 'state' and 'draft' in triplet[2]:
                            if isinstance(triplet[2], list):
                                new_t = ('state', triplet[1],
                                         triplet[2] + ['rejected'])
                            else:
                                if triplet[1] == '=':
                                    new_t = ('state', 'in',
                                             [triplet[2], 'rejected'])
                                elif triplet[1] in ['!=', '<>']:
                                    new_t = ('state', 'not in',
                                             [triplet[2], 'rejected'])
                            if modifiers and not attrs:
                                new_t = list(new_t)
                            new_val.append(new_t)
                        else:
                            new_val.append(triplet)
                    mod_dict[attr] = new_val
                    mod_dict_str = str(mod_dict)
                    attrs_dict_str = str(mod_dict)
                    if modifiers:
                        mod_dict_str = mod_dict_str.replace('True', 'true')
                        mod_dict_str = mod_dict_str.replace('False', 'false')
                        mod_dict_str = mod_dict_str.replace("'", "&quot;")
                        mod_dict_str = mod_dict_str.replace("(", "[")
                        mod_dict_str = mod_dict_str.replace(")", "]")
                if modifiers:
                    node.set('modifiers', mod_dict_str)
                node.set('attrs', attrs_dict_str)
        res['arch'] = etree.tostring(doc)
        res['arch'] = res['arch'].replace('&amp;', '&')  # ?
        return res

    @api.multi
    def queue_invoice(self):
        self.ensure_one()
        self.write({'state': 'waiting'})
        self.queue('validate_invoice')
