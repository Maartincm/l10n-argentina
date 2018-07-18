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

import re

from openerp import _, api, exceptions, fields, models
from openerp.exceptions import except_orm

import logging
_logger = logging.getLogger(__name__)

__author__ = "Sebastian Kennedy <skennedy@e-mips.com.ar>"


class account_invoice(models.Model):
    _name = "account.invoice"
    _inherit = "account.invoice"

    aut_cae = fields.Boolean('Autorizar', default=False,
                             help='Pedido de autorizacion a la AFIP')
    cae = fields.Char(
        string='CAE/CAI', size=32, required=False,
        help='CAE (Codigo de Autorizacion Electronico assigned by AFIP.)')
    cae_due_date = fields.Date('CAE Due Date', required=False,
                               help='Fecha de vencimiento del CAE')
    associated_inv_ids = fields.Many2many(
        'account.invoice', 'account_invoice_associated_rel',
        'invoice_id', 'refund_debit_id')

    # Campos para facturas de exportacion. Aca ninguno es requerido,
    # eso lo hacemos en la vista ya que depende de
    # si es o no factura de exportacion
    export_type_id = fields.Many2one('wsfex.export_type.codes', 'Export Type')
    dst_country_id = fields.Many2one('wsfex.dst_country.codes', 'Dest Country')
    dst_cuit_id = fields.Many2one('wsfex.dst_cuit.codes', 'Country CUIT')
    shipping_perm_ids = fields.One2many('wsfex.shipping.permission',
                                        'invoice_id', 'Shipping Permissions')
    incoterm_id = fields.Many2one(
        'stock.incoterms', 'Incoterm',
        help="International Commercial Terms are a series of predefined commercial terms used in international transactions.")  # noqa
    wsfe_request_ids = fields.One2many('wsfe.request.detail', 'name')
    wsfex_request_ids = fields.One2many('wsfex.request.detail', 'invoice_id')

    @api.multi
    def onchange_partner_id(self, type, partner_id, date_invoice=False,
                            payment_term=False, partner_bank_id=False,
                            company_id=False):
        res = super(account_invoice, self).onchange_partner_id(
            type, partner_id, date_invoice=False, payment_term=False,
            partner_bank_id=False, company_id=False)

        if partner_id:
            partner = self.env['res.partner'].browse(partner_id)
            country_id = partner.country_id.id or False
            if country_id:
                dst_country = self.env['wsfex.dst_country.codes'].search(
                    [('country_id', '=', country_id)])

                if dst_country:
                    res['value'].update({'dst_country_id': dst_country[0].id})
        return res

    # Esto lo hacemos porque al hacer una nota de credito,
    # no le setea la fiscal_position.
    # Ademas, seteamos el comprobante asociado
    def refund(self, cr, uid, ids, date=None, period_id=None,
               description=None, journal_id=None, context=None):
        new_ids = super(account_invoice, self).refund(
            cr, uid, ids, date, period_id,
            description, journal_id, context=context)

        for refund_id in new_ids:
            vals = {}
            refund = self.browse(cr, uid, refund_id)
            invoice = self.browse(cr, uid, ids[0])
            if not refund.fiscal_position:
                fiscal_position = refund.partner_id.property_account_position
                vals = {'fiscal_position': fiscal_position.id}

            # Agregamos el comprobante asociado y otros campos necesarios
            # si es de exportacion
            if not invoice.local:
                vals['export_type_id'] = invoice.export_type_id.id
                vals['dst_country_id'] = invoice.dst_country_id.id
                vals['dst_cuit_id'] = invoice.dst_cuit_id.id
                vals['associated_inv_ids'] = [(4, invoice.id)]
            vals['associated_inv_ids'] = [(4, invoice.id)]

            if vals:
                self.write(cr, uid, refund_id, vals)
        return new_ids

    @api.model
    def _check_fiscal_values(self):
        self.ensure_one()
        inv = self
        # Si es factura de cliente
        denomination_id = inv.denomination_id and \
            inv.denomination_id.id or False
        if inv.type in ('out_invoice', 'out_refund'):
            if not denomination_id:
                raise except_orm(_('Error!'),
                                 _('Denomination not set in invoice'))

            if denomination_id not in inv.pos_ar_id.denomination_ids.ids:
                err = _('Point of sale has not the same ' +
                        'denomination as the invoice.')
                raise except_orm(_('Error!'), err)

            # Chequeamos que la posicion fiscal y la denomination_id coincidan

            if inv.fiscal_position.denomination_id.id != denomination_id:
                err = _('The invoice denomination does ' +
                        'not corresponds with this fiscal position.')
                raise except_orm(_('Error'), err)

        # Si es factura de proveedor
        else:
            if not denomination_id:
                raise except_orm(_('Error!'),
                                 _('Denomination not set in invoice'))

            # Chequeamos que la posicion fiscal y la denomination_id coincidan
            if inv.fiscal_position.denom_supplier_id.id != \
                    inv.denomination_id.id:
                err = _('The invoice denomination does not ' +
                        'corresponds with this fiscal position.')
                raise except_orm(_('Error'), err)
        # Chequeamos que la posicion fiscal de la factura
        # y la del cliente tambien coincidan
        if inv.fiscal_position.id != \
                inv.partner_id.property_account_position.id:
            err = _('The invoice fiscal position is not ' +
                    'the same as the partner\'s fiscal position.')
            raise except_orm(_('Error'), err)
        return True

    @api.multi
    def _get_voucher_type(self):
        self.ensure_one()
        voucher_type_obj = self.env['wsfe.voucher_type']

        # Obtenemos el tipo de comprobante
        voucher_type = voucher_type_obj.get_voucher_type(self)
        return voucher_type

    @api.multi
    def _get_pos(self):
        self.ensure_one()
        try:
            pos = self.split_number()[0]
        except Exception:
            if not self.pos_ar_id:
                err = _("Pos not found for invoice `%s` (id: %s)") % \
                    (self.internal_number, self.id)
                raise except_orm(_("Error!"), err)
            pos = int(self.pos_ar_id.name)
        return pos

    @api.multi
    def _get_next_wsfe_number(self, conf=False):
        self.ensure_one()
        if not conf:
            conf = self.get_ws_conf()
        inv = self
        tipo_cbte = self._get_voucher_type()
        try:
            pto_vta = int(inv.pos_ar_id.name)
        except ValueError:
            err = _('El nombre del punto de venta tiene que ser numerico')
            raise except_orm(_('Error'), err)

        last = conf.get_last_voucher(pto_vta, tipo_cbte)

        return int(last + 1)

    @api.multi
    def get_last_date_invoice(self):
        self.ensure_one()
        q = """
        SELECT MAX(date_invoice)
        FROM account_invoice
        WHERE internal_number ~ '^[0-9]{4}-[0-9]{8}$'
            AND pos_ar_id = %(pos_id)s
            AND state in %(state)s
            AND type = %(type)s
            AND is_debit_note = %(is_debit_note)s
        """
        q_vals = {
            'pos_id': self.pos_ar_id.id,
            'state': ('open', 'paid', 'cancel',),
            'type': self.type,
            'is_debit_note': self.is_debit_note,
        }
        self.env.cr.execute(q, q_vals)
        last_date = self.env.cr.fetchone()
        if last_date and last_date[0]:
            last_date = last_date[0]
        return last_date

    @api.multi
    def get_next_invoice_number(self):
        """
        Funcion para obtener el siguiente numero de comprobante
        correspondiente en el sistema
        """
        self.ensure_one()
        invoice = self
        cr = self.env.cr
        # Obtenemos el ultimo numero de comprobante
        # para ese pos y ese tipo de comprobante
        q = """
        SELECT MAX(TO_NUMBER(
            SUBSTRING(internal_number FROM '[0-9]{8}$'), '99999999')
            )
        FROM account_invoice
        WHERE internal_number ~ '^[0-9]{4}-[0-9]{8}$'
            AND pos_ar_id = %(pos_id)s
            AND state in %(state)s
            AND type = %(type)s
            AND is_debit_note = %(is_debit_note)s
        """
        q_vals = {
            'pos_id': invoice.pos_ar_id.id,
            'state': ('open', 'paid', 'cancel',),
            'type': invoice.type,
            'is_debit_note': invoice.is_debit_note,
        }
        cr.execute(q, q_vals)
        last_number = cr.fetchone()
        self.env.invalidate_all()

        # Si no devuelve resultados, es porque es el primero
        if not last_number or not last_number[0]:
            next_number = 1
        else:
            next_number = last_number[0] + 1

        return int(next_number)

    # Heredado para no cancelar si es una factura electronica
    @api.multi
    def action_cancel(self):
        for inv in self:
            if inv.aut_cae:
                err = _("You cannot cancel an Electronic Invoice " +
                        "because it has been informed to AFIP.")
                raise exceptions.ValidationError(err)
        return super(account_invoice, self).action_cancel()

    @api.multi
    def action_number(self):

        next_number = None
        invoice_vals = {}
        invtype = None

        # TODO: not correct fix but required a fresh values before reading it.
        # Esto se usa para forzar a que recalcule los campos funcion
        # self.write({})

        for obj_inv in self:

            invtype = obj_inv.type
            # Chequeamos si es local por medio de la posicion fiscal
            local = obj_inv.fiscal_position.local

            # Si es local o de cliente
            if local or invtype in ('out_invoice', 'out_refund'):
                # Chequeamos los valores fiscales
                self._check_fiscal_values()

            # si el usuario no ingreso un numero,
            # busco el ultimo y lo incremento , si no hay ultimo va 1.
            # si el usuario hizo un ingreso dejo ese numero
            internal_number = obj_inv.internal_number
            next_number = False

            # Si son de Cliente
            if invtype in ('out_invoice', 'out_refund'):

                pos_ar = obj_inv.pos_ar_id
                next_number = self.get_next_invoice_number()

                conf = self.get_ws_conf()
                if conf:
                    invoice_vals['aut_cae'] = True

                # Si no es Factura Electronica...
                else:
                    # Nos fijamos si el usuario dejo en
                    # blanco el campo de numero de factura
                    if obj_inv.internal_number:
                        internal_number = obj_inv.internal_number

                # Lo ponemos como en Proveedores, o sea, A0001-00000001
                if not internal_number:
                    internal_number = '%s-%08d' % (pos_ar.name, next_number)

                m = re.match('^[0-9]{4}-[0-9]{8}$', internal_number)
                if not m:
                    err = _('The Invoice Number should be the ' +
                            'format XXXX-XXXXXXXX')
                    raise except_orm(_('Error'), err)

                # Escribimos el internal number
                invoice_vals['internal_number'] = internal_number

            # Si son de Proveedor
            else:
                if not obj_inv.internal_number:
                    err = _('The Invoice Number should be filled')
                    raise except_orm(_('Error'), err)

                if local:
                    m = re.match('^[0-9]{4}-[0-9]{8}$',
                                 obj_inv.internal_number)
                    if not m:
                        err = _('The Invoice Number should be ' +
                                'the format XXXX-XXXXXXXX')
                        raise except_orm(_('Error'), err)

            # Escribimos los campos necesarios de la factura
            obj_inv.write(invoice_vals)

            # invoice_name = obj_inv.name_get()[0][1]
            # reference = obj_inv.reference or ''
            # if not reference:
            #     ref = invoice_name
            # else:
            #     ref = '%s [%s]' % (invoice_name, reference)

            # Actulizamos el campo reference del move_id
            # correspondiente a la creacion de la factura
            # obj_inv._update_reference(ref)

        return True

    @api.multi
    def action_move_create(self):
        res = super(account_invoice, self).action_move_create()
        for inv in self:
            invoice_name = inv.name_get()[0][1]
            reference = inv.reference or ''
            if not reference:
                ref = invoice_name
            else:
                ref = '%s [%s]' % (invoice_name, reference)

            # Actulizamos el campo reference del move_id
            # correspondiente a la creacion de la factura
            inv._update_reference(ref)
        return res

    @api.model
    def hook_add_taxes(self, inv, detalle):
        return detalle

    def _sanitize_taxes(self, invoice):

        # Sanitize taxes: puede pasar que tenga un
        # IVA con un monto de impuesto 0.0
        # Esto pasa porque el monto sobre el que se aplica es muy chico.
        # Quitamos el impuesto
        zero_taxes = invoice.tax_line.filtered(lambda x: x.amount == 0.0)

        if not zero_taxes:
            return

        tax_in_zero = zero_taxes.mapped(lambda x: x.tax_id.id)
        lines_no_taxes = invoice.invoice_line.filtered(
                lambda x: x.invoice_line_tax_id.id in tax_in_zero)

        tax_remove = map(lambda x: (3, x, _), tax_in_zero)
        lines_no_taxes.write({'invoice_line_tax_id': tax_remove})

        invoice.button_reset_taxes()

    @api.multi
    def action_aut_cae(self):

        for inv in self:
            if not inv.aut_cae:
                return True

            self._sanitize_taxes(self)
            ws = self.new_ws()

            try:
                invoices_approved = ws.send_invoice(inv)

                for invoice_id, invoice_vals in invoices_approved.iteritems():
                    inv_obj = self.env['account.invoice'].browse(invoice_id)
                    inv_obj.write(invoice_vals)
                # Commit the info that was written to the invoice and
                # given by AFIP to prevent desynchronizations
                self.env.cr.commit()
            except except_orm as e:
                raise
            except Exception as e:
                err = _('Error received was: \n %s') % repr(e)
                raise except_orm(
                    _('WSFE Validation Error'), err)
            finally:
                # Creamos el wsfe.request con otro cursor,
                # porque puede pasar que
                # tengamos una excepcion e igualmente,
                # tenemos que escribir la request
                # Sino al hacer el rollback se pierde hasta el wsfe.request
                with api.Environment.manage():
                    new_cr = self.pool.cursor()
                    new_env = api.Environment(new_cr, self.env.user.id,
                                              self.env.context)
                    ws.log_request(new_env)
                    new_cr.commit()
                    new_cr.close()
        return True

    @api.one
    def wsfe_relate_invoice(self, pos, number, date_invoice,
                            cae, cae_due_date):
        # Tomamos la factura y mandamos a realizar
        # el asiento contable primero.
        self.action_move_create()

        invoice_vals = {
            'internal_number': '%04d-%08d' % (pos, number),
            'date_invoice': date_invoice,
            'cae': cae,
            'cae_due_date': cae_due_date,
        }

        # Escribimos los campos necesarios de la factura
        self.write(invoice_vals)

        invoice_name = self.name_get()[0][1]
        if not self.reference:
            ref = invoice_name
        else:
            ref = '%s [%s]' % (invoice_name, self.reference)

        # Actulizamos el campo reference del move_id
        # correspondiente a la creacion de la factura
        self._update_reference(ref)

        # Llamamos al workflow para que siga su curso
        self.signal_workflow('invoice_massive_open')
        return

###############################################################################

    @api.multi
    def new_ws(self, conf=False):
        if not conf:
            conf = self.get_ws_conf()
        ws = conf._webservice_class(conf.url)
        return ws

    @api.multi
    def ws_auth(self, ws=False, conf=False):
        # TODO WSAA To easywsy and this could float between WSAA & WSFE
        if not conf:
            conf = self.get_ws_conf()
        token, sign = conf.wsaa_ticket_id.get_token_sign()
        auth = {
            'Token': token,
            'Sign': sign,
            'Cuit': conf.cuit
        }
        if not ws:
            ws = conf._webservice_class(conf.url)
        ws.login('Auth', auth)
        return ws

    @api.multi
    def complete_date_invoice(self):
        for inv in self:
            if not inv.date_invoice:
                inv.write({
                    'date_invoice': fields.Date.context_today(self),
                })

    @api.multi
    def check_invoice_total(self, calculated_total):
        # Chequeamos que el Total calculado por Odoo, se corresponda
        # con el total calculado por nosotros, tal vez puede haber un error
        # de redondeo
        obj_precision = self.env['decimal.precision']
        prec = obj_precision.precision_get('Account')
        if round(calculated_total, prec) != round(self.amount_total, prec):
            raise except_orm(
                _('Error in amount_total!'),
                _("The total amount of the invoice does not " +
                  "match the total calculated.\n" +
                  "Maybe there is a rounding error!. " +
                  "(Amount Calculated: %f)") % (calculated_total))

    @api.multi
    def get_ws_conf(self):
        wsfe_conf_obj = self.env['wsfe.config']
        wsfex_conf_obj = self.env['wsfex.config']
        local_list = self.mapped('local')
        if len(list(set(local_list))) != 1:
            err = _("Trying to get the WSFE config for invoices mixed " +
                    "between local and not local")
            raise except_orm(_("WSFE Error"), err)
        local = local_list[0]
        ctx = self.env.context.copy()
        if local:
            ctx['without_raise'] = True
        wsfe_conf = wsfe_conf_obj.with_context(ctx).get_config()
        if not local:
            ctx = {}
        wsfex_conf = wsfex_conf_obj.with_context(ctx).get_config()
        pos_ar_list = self.mapped('pos_ar_id')
        if len(list(set(local_list))) != 1:
            err = _("Trying to get the WSFE config for invoices that " +
                    "belong to different points of sale")
            raise except_orm(_("WSFE Error"), err)
        pos_ar = pos_ar_list[0]
        # Chequeamos si corresponde Factura Electronica
        # Aca nos fijamos si el pos_ar_id tiene
        # factura electronica asignada
        confs = filter(lambda c: pos_ar in c.point_of_sale_ids,
                       [wsfe_conf, wsfex_conf])

        if len(confs) > 1:
            err = _("There is more than one configuration " +
                    "with this POS %s") % pos_ar.name
            raise except_orm(_("WSFE Error"), err)

        if confs:
            conf = confs[0]
        elif not ctx['without_raise']:
            err = _("There is no configuration for this " +
                    "POS %s") % pos_ar.name
            raise except_orm(_("WSFE Error"), err)
        else:
            conf = False
        return conf

    @api.multi
    def split_number(self):
        try:
            pos, numb = self.internal_number.split('-')
        except (ValueError, AttributeError):
            raise except_orm(
                _("Error!"),
                _("Wrong Number format for invoice id: `%s`" % self.id))
        if not pos:
            raise except_orm(
                _("Error!"),
                _("Wrong POS for invoice id: `%s`" % self.id))
        if not numb:
            raise except_orm(
                _("Error!"),
                _("Wrong Number Sequence for invoice id: `%s`" % self.id))
        try:
            pos = int(pos)
        except ValueError:
            raise except_orm(
                _("Error!"),
                _("Wrong POS `%s` for invoice id: `%s`" % (pos, self.id)))
        try:
            numb = int(numb)
        except ValueError:
            raise except_orm(
                _("Error!"),
                _("Wrong Number Sequence `%s` for invoice id: `%s`" %
                  (numb, self.id)))
        return pos, numb

    @api.multi
    def get_currency_code(self):
        # Obtenemos la moneda de la factura
        # Lo hacemos por el wsfex_config, por cualquiera de ellos
        # si es que hay mas de uno
        self.ensure_one()
        currency_code_obj = self.env['wsfex.currency.codes']
        currency_code_ids = currency_code_obj.search(
            [('currency_id', '=', self.currency_id.id)])

        if not currency_code_ids:
            raise except_orm(
                _("WSFE Error!"),
                _("Currency has to be configured correctly " +
                  "in WSFEX Configuration."))
        currency_code = currency_code_ids[0].code
        return currency_code


class account_invoice_tax(models.Model):
    _name = "account.invoice.tax"
    _inherit = "account.invoice.tax"

    @api.multi
    def hook_compute_invoice_taxes(self, invoice, tax_grouped):
        tax_obj = self.env['account.tax']
        currency = invoice.currency_id.with_context(
            date=invoice.date_invoice or fields.Date.context_today(invoice))

        for t in tax_grouped.values():
            # Para solucionar el problema del redondeo con AFIP
            ta = tax_obj.browse(t['tax_id'])
            t['amount'] = t['base'] * ta.amount
            t['tax_amount'] = t['base_amount'] * ta.amount

            t['base'] = currency.round(t['base'])
            t['amount'] = currency.round(t['amount'])
            t['base_amount'] = currency.round(t['base_amount'])
            t['tax_amount'] = currency.round(t['tax_amount'])

        return super(account_invoice_tax, self).\
            hook_compute_invoice_taxes(invoice, tax_grouped)
