#' -*- coding: utf-8 -*-
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

from osv import osv, fields
from tools.translate import _
from wsfe_suds import WSFEv1 as wsfe
from datetime import datetime
import time


class wsfe_tax_codes(osv.osv):
    _name = "wsfe.tax.codes"
    _description = "Tax Codes"
    _columns = {
        'code' : fields.char('Code', required=False, size=4),
        'name' : fields.char('Desc', required=True, size=64),
        'to_date' : fields.date('Effect Until'),
        'from_date' : fields.date('Effective From'),
        'tax_id' : fields.many2one('account.tax','Account Tax'),
        'tax_code_id': fields.many2one('account.tax.code', 'Account Tax Code'),
        'wsfe_config_id' : fields.many2one('wsfe.config','WSFE Configuration'),
        'from_afip': fields.boolean('From AFIP'),
        'exempt_operations': fields.boolean('Exempt Operations', help='Check it if this VAT Tax corresponds to vat tax exempts operations, such as to sell books, milk, etc. The taxes with this checked, will be reported to AFIP as  exempt operations (base amount) without VAT applied on this'),
    }


class wsfe_config(osv.osv):
    _name = "wsfe.config"
    _description = "Configuration for WSFE"
    _rec_name = 'cuit'

    _columns = {
        'cuit': fields.related('company_id', 'partner_id', 'vat', type='char', string='Cuit'),
        'url' : fields.char('URL for WSFE', size=60, required=True),
        'point_of_sale_ids': fields.many2many('pos.ar', 'pos_ar_wsfe_rel', 'wsfe_config_id', 'pos_ar_id', 'Points of Sale'),
        'vat_tax_ids' : fields.one2many('wsfe.tax.codes', 'wsfe_config_id' ,'Taxes', domain=[('from_afip', '=', True)]),
        'exempt_operations_tax_ids' : fields.one2many('wsfe.tax.codes', 'wsfe_config_id' ,'Taxes', domain=[('from_afip', '=', False), ('exempt_operations', '=', True)]),
        'wsaa_ticket_id' : fields.many2one('wsaa.ta', 'Ticket Access'),
        'company_id' : fields.many2one('res.company', 'Company Name' , required=True),
    }

    _sql_constraints = [
        ('company_uniq', 'unique (company_id)', 'The configuration must be unique per company !')
    ]

    _defaults = {
        'company_id' : lambda self, cr, uid, context=None: self.pool.get('res.users')._get_company(cr, uid, context=context),
        }

    def create(self, cr, uid, vals, context):

        # Creamos tambien un TA para este servcio y esta compania
        ta_obj = self.pool.get('wsaa.ta')
        wsaa_obj = self.pool.get('wsaa.config')
        service_obj = self.pool.get('afipws.service')

        # Buscamos primero el wsaa que corresponde a esta compania
        # porque hay que recordar que son unicos por compania
        wsaa_ids = wsaa_obj.search(cr, uid, [('company_id','=', vals['company_id'])], context=context)
        service_ids = service_obj.search(cr, uid, [('name','=', 'wsfe')], context=context)
        if wsaa_ids:
            ta_vals = {
                'name': service_ids[0],
                'company_id': vals['company_id'],
                'config_id' : wsaa_ids[0],
                }

            ta_id = ta_obj.create(cr, uid, ta_vals, context)
            vals['wsaa_ticket_id'] = ta_id

        return super(wsfe_config, self).create(cr, uid, vals, context)

    def get_config(self, cr, uid):
        # Obtenemos la compania que esta utilizando en este momento este usuario
        res = self.pool.get('res.users').get_current_company(cr, uid)
        if not res:
            raise osv.except_osv(_('Company Error!'), _('There is no company being used by this user'))

        company_id = res[0][0]

        ids = self.search(cr, uid, [('company_id','=',company_id)])
        if not ids:
            raise osv.except_osv(_('WSFE Config Error!'), _('There is no WSFE configuration set to this company'))

        return self.browse(cr, uid, ids[0])


    def check_errors(self, cr, uid, res, raise_exception=True, context=None):
        msg = ''
        if 'errors' in res:
            errors = [error.msg for error in res['errors']]
            err_codes = [str(error.code) for error in res['errors']]
            msg = ' '.join(errors)
            msg = msg + ' Codigo/s Error:' + ' '.join(err_codes)

            if msg != '' and raise_exception:
                raise osv.except_osv(_('WSFE Error!'), msg)

        return msg

    def check_observations(self, cr, uid, res, context):
        msg = ''
        if 'observations' in res:
            observations = [obs.msg for obs in res['observations']]
            obs_codes = [str(obs.code) for obs in res['observations']]
            msg = ' '.join(observations)
            msg = msg + ' Codigo/s Observacion:' + ' '.join(obs_codes)

            # Escribimos en el log del cliente web
            self.log(cr, uid, None, msg, context)

        return msg

    def get_invoice_CAE(self, cr, uid, ids, invoice_ids, pos, voucher_type, details, context={}):
        ta_obj = self.pool.get('wsaa.ta')

        conf = self.browse(cr, uid, ids)[0]
        token, sign = ta_obj.get_token_sign(cr, uid, [conf.wsaa_ticket_id.id], context=context)

        _wsfe = wsfe(conf.cuit, token, sign, conf.url)
        res = _wsfe.fe_CAE_solicitar(pos, voucher_type, details)

        return res

    def _log_wsfe_request(self, cr, uid, ids, pos, voucher_type_code, details, res, context=None):
        wsfe_req_obj = self.pool.get('wsfe.request')
        voucher_type_obj = self.pool.get('wsfe.voucher_type')
        voucher_type_ids = voucher_type_obj.search(cr, uid, [('code','=',voucher_type_code)])
        voucher_type_name = voucher_type_obj.read(cr, uid, voucher_type_ids, ['name'])[0]['name']

        req_details = []
        for index, comp in enumerate(res['Comprobantes']):
            detail = details[index]

            det = {
                'name': detail['invoice_id'],
                'concept': str(detail['Concepto']),
                'doctype': detail['DocTipo'], # TODO: Poner aca el nombre del tipo de documento
                'docnum': str(detail['DocNro']),
                'voucher_number': comp['CbteHasta'],
                'voucher_date': comp['CbteFch'],
                'amount_total': detail['ImpTotal'],
                'cae': comp['CAE'],
                'cae_duedate': comp['CAEFchVto'],
                'result': comp['Resultado'],
                'observations': '\n'.join(comp['Observaciones']),
            }

            req_details.append((0, 0, det))

        # Chequeamos el reproceso
        reprocess = False
        if res['Reproceso'] == 'S':
            reprocess = True

        vals = {
            'voucher_type': voucher_type_name,
            'nregs': len(details),
            'pos_ar': '%04d' % pos,
            'date_request': time.strftime('%Y-%m-%d %H:%M:%S'),
            'result': res['Resultado'],
            'reprocess': reprocess,
            'errors': '\n'.join(res['Errores']),
            'detail_ids': req_details,
            }

        return wsfe_req_obj.create(cr, uid, vals)

    def get_last_voucher(self, cr, uid, ids, pos, voucher_type, context={}):
        ta_obj = self.pool.get('wsaa.ta')

        conf = self.browse(cr, uid, ids)[0]
        token, sign = ta_obj.get_token_sign(cr, uid, [conf.wsaa_ticket_id.id], context=context)

        _wsfe = wsfe(conf.cuit, token, sign, conf.url)
        res = _wsfe.fe_comp_ultimo_autorizado(pos, voucher_type)

        self.check_errors(cr, uid, res, context=context)
        self.check_observations(cr, uid, res, context=context)
        last = res['response'].CbteNro
        return last

    def read_tax(self, cr, uid , ids , context={}):
        ta_obj = self.pool.get('wsaa.ta')

        conf = self.browse(cr, uid, ids)[0]
        token, sign = ta_obj.get_token_sign(cr, uid, [conf.wsaa_ticket_id.id], context=context)

        _wsfe = wsfe(conf.cuit, token, sign, conf.url)
        res = _wsfe.fe_param_get_tipos_iva()

        wsfe_tax_obj = self.pool.get('wsfe.tax.codes')

        # Chequeamos los errores
        msg = self.check_errors(cr, uid, res, raise_exception=False, context=context)
        if msg:
            # TODO: Hacer un wrapping de los errores, porque algunos son
            # largos y se imprimen muy mal en pantalla
            raise osv.except_osv(_('Error reading taxes'), msg)

        #~ Armo un lista con los codigos de los Impuestos
        for r in res['response']:
            res_c = wsfe_tax_obj.search(cr, uid , [('code','=', r.Id )])

            #~ Si tengo no los codigos de esos Impuestos en la db, los creo
            if not len(res_c):
                fd = datetime.strptime(r.FchDesde, '%Y%m%d')
                try:
                    td = datetime.strptime(r.FchHasta, '%Y%m%d')
                except ValueError:
                    td = False

                wsfe_tax_obj.create(cr, uid , {'code': r.Id, 'name': r.Desc, 'to_date': td,
                        'from_date': fd, 'wsfe_config_id': ids[0], 'from_afip': True } , context={})
            #~ Si los codigos estan en la db los modifico
            else :
                fd = datetime.strptime(r.FchDesde, '%Y%m%d')
                #'NULL' ?? viene asi de fe_param_get_tipos_iva():
                try:
                    td = datetime.strptime(r.FchHasta, '%Y%m%d')
                except ValueError:
                    td = False

                wsfe_tax_obj.write(cr, uid , res_c[0] , {'code': r.Id, 'name': r.Desc, 'to_date': td ,
                    'from_date': fd, 'wsfe_config_id': ids[0], 'from_afip': True } )

        return True

wsfe_config()
wsfe_tax_codes()

class wsfe_voucher_type(osv.osv):
    _name = "wsfe.voucher_type"
    _description = "Voucher Type for Electronic Invoice"

    _columns = {
        'name': fields.char('Name', size=64, required=True, readonly=False, help='Voucher Type, eg.: Factura A, Nota de Credito B, etc.'),
        'code': fields.char('Code', size=4, required=True, help='Internal Code assigned by AFIP for voucher type'),

        'voucher_model': fields.selection([
            ('invoice','Factura/NC/ND'),
            ('voucher','Recibo'),],'Voucher Model', select=True, required=True),

        'document_type' : fields.selection([
            ('out_invoice','Factura'),
            ('out_refund','Nota de Credito'),
            ('out_debit','Nota de Debito'),
            ],'Document Type', select=True, required=True, readonly=False),

        'denomination_id': fields.many2one('invoice.denomination', 'Denomination', required=False),
    }

    def get_voucher_type(self, cr, uid, voucher, context=None):

        # Chequeamos el modelo
        voucher_model = None
        model = voucher._table_name

        if model == 'account.invoice':
            voucher_model = 'invoice'

            denomination_id = voucher.denomination_id.id
            type = voucher.type
            #if type == 'out_invoice':
                # TODO: Activar esto para ND
                #if voucher.debit_note:
                    #type = 'out_debit'

            res = self.search(cr, uid, [('voucher_model','=',voucher_model), ('document_type','=',type), ('denomination_id','=',denomination_id)], context=context)

            if not len(res):
                raise osv.except_osv(_("Voucher type error!"), _("There is no voucher type that corresponds to this object"))

            if len(res) > 1:
                raise osv.except_osv(_("Voucher type error!"), _("There is more than one voucher type that corresponds to this object"))

            return self.read(cr, uid, res[0], ['code'], context=context)['code']

        elif model == 'account.voucher':
            voucher_model = 'voucher'

        return None

wsfe_voucher_type()
