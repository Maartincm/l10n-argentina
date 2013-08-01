# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (c) 2013 E-MIPS (http://www.e-mips.com.ar) All Rights Reserved.
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
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
from osv import osv
from tools.translate import _
import netsvc

class account_invoice_confirm(osv.osv_memory):
    """
    This wizard will confirm the all the selected draft invoices
    """
    _name = "account.invoice.confirm"
    _inherit = "account.invoice.confirm"

    def invoice_confirm(self, cr, uid, ids, context=None):
        wsfe_conf_obj = self.pool.get('wsfe.config')
        voucher_type_obj = self.pool.get('wsfe.voucher_type')
        inv_obj = self.pool.get('account.invoice')
        move_obj = self.pool.get('account.move')
        wf_service = netsvc.LocalService('workflow')

        conf = wsfe_conf_obj.get_config(cr, uid)

        if context is None:
            context = {}
        #data_inv = inv_obj.read(cr, uid, context['active_ids'], ['state', 'pos_ar_id', 'type'], context=context)
        data_inv = inv_obj.browse(cr, uid, context['active_ids'], context=context)

        # Primero tenemos que chequear si al menos alguna de las facturas se debe hacer por factura electronica
        electronic_pos_ids = [p.id for p in conf.point_of_sale_ids]
        pos_electronic = [invoice.pos_ar_id.id in electronic_pos_ids for invoice in data_inv]

        # Si se estan validando facturas que ninguna pertenece a punto de venta electronico, seguimos como siempre
        if not any(pos_electronic):
            return super(account_invoice_confirm, self).invoice_confirm(cr, uid, ids, context)

        # Si estamos en este punto es porque alguna o todas es electronica
        if not all(pos_electronic):
            raise osv.except_osv(_('WSFE Error!'), _('You are trying to validate several invoices but not all of them belongs to an electronic point of sale'))

        # La condicion para que se pueda validar un lote de facturas electronicas
        # es que deben ser del mismo Punto de Venta, el mismo Tipo de Comprobante
        # y obviamente estar en estado 'Draft'
        pos_ids = [invoice.pos_ar_id.id for invoice in data_inv]
        same_pos = len(set(pos_ids))==1

        # Son todas del mismo Punto de Venta?
        if not same_pos:
            raise osv.except_osv(_('WSFE Error!'), _('You are trying to validate several invoices but not all of them belongs to the same point of sale'))

        draft_state = [invoice.state=='draft' for invoice in data_inv]

        # Estan todas en estado 'Draft'?
        if not all(draft_state):
            raise osv.except_osv(_('WSFE Error!'), _("You are trying to validate several invoices but not all of them are in 'draft' state"))

        tipos = []
        for inv in data_inv:
            tipo_cbte = voucher_type_obj.get_voucher_type(cr, uid, inv, context=context)
            tipos.append(tipo_cbte)

        same_type = len(set(tipos)) == 1

        # Son todas el mismo Tipo de Comprobante?
        if not same_type:
            raise osv.except_osv(_('WSFE Error!'), _("You are trying to validate several invoices but not all of them are the same type. For example, all Customer Invoices or all Customer Refund"))

        # Tomamos las facturas y mandamos a realizar los asientos contables primero.
        inv_obj.action_move_create(cr, uid, context['active_ids'], context)

        # Preparamos el lote de facturas para la AFIP
        next_number = inv_obj._get_next_number(cr, uid, data_inv[0], context=context)
        num = "%s-%08d" % (invoice.pos_ar_id.name, next_number)
        context['first_num'] = num
        fe_det_req = inv_obj.wsfe_invoice_prepare_detail(cr, uid, context['active_ids'], context)

        # Llamamos a la funcion para validar contra la AFIP
        pos = int(invoice.pos_ar_id.name)

        result = wsfe_conf_obj.get_invoice_CAE(cr, uid, [conf.id], context['active_ids'], pos, tipo_cbte, fe_det_req, context=context)
        context['raise-exception'] = False
        invoices_approbed = inv_obj._parse_result(cr, uid, context['active_ids'], result, context=context)

        req_id = wsfe_conf_obj._log_wsfe_request(cr, uid, ids, pos, tipo_cbte, fe_det_req, result)

        invoices_not_approbed = [j for j in context['active_ids'] if j not in invoices_approbed]

        # Seguimos adelante con el workflow para todas las que fueron aprobadas
        for invoice in inv_obj.browse(cr, uid, invoices_approbed):
            ref = invoice.internal_number
            inv_obj._update_reference(cr, uid, invoice, ref, context)

            # Como sacamos el post de action_move_create, lo tenemos que poner aqui
            # Lo sacamos para permitir la validacion por lote. Ver wizard account.invoice.confirm
            move_id = invoice.move_id and invoice.move_id.id or False
            self.pool.get('account.move').post(cr, uid, [move_id], context={'invoice':invoice})

            # Llamamos al workflow para que siga su curso
            wf_service.trg_validate(uid, 'account.invoice', invoice.id, 'invoice_massive_open', cr)

        # Borramos los asientos contables de las facturas no aprobadas
        move_ids = []
        for invoice in inv_obj.browse(cr, uid, invoices_not_approbed):
            move_id = invoice.move_id.id
            move_ids.append(move_id)
            # Y borramos otros campos que ya no deben estar seteados
            vals = {'move_id': False, 'date_invoice': False}
            inv_obj.write(cr, uid, invoice.id, vals)

        move_obj.unlink(cr, uid, move_ids)

        # TODO: Ver que pasa con las account_analytic_lines
        return {
            'name': _('WSFE Request'),
            'domain' : "[('id','=',%s)]"%(req_id),
            'view_type': 'form',
            'view_mode': 'tree,form',
            'res_model': 'wsfe.request',
            'view_id': False,
            'type': 'ir.actions.act_window',
        }

account_invoice_confirm()
