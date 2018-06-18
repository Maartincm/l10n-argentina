from easywsy import WebService, wsapi
from datetime import datetime
from openerp.exceptions import except_orm
from openerp import _

import time
import logging
_logger = logging.getLogger(__name__)


class Error:

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg

    def __str__(self):
        return '%s (Err. %s)' % (self.msg, self.code)


class Event:

    def __init__(self, code, msg):
        self.code = code
        self.msg = msg

    def __str__(self):
        return '%s (Evento %s)' % (self.msg, self.code)


class WSFE2(WebService):

    def parse_invoices(self, invoices):
        reg_qty = len(invoices)
        voucher_type = invoices[0]._get_voucher_type()
        pos = invoices[0].split_number()[0]
        data = {
            'FECAESolicitar': {
                'FeCAEReq': {
                    'FeCabReq': {
                        'CbteTipo': voucher_type,
                        'PtoVta': pos,
                        'CantReg': reg_qty,
                    },
                    'FeDetReq': {
                        'FECAEDetRequest': [],
                    },
                },
            },
        }
        details_array = data['FECAESolicitar']['FeCAEReq'][
            'FeDetReq']['FECAEDetRequest']
        for inv in invoices:
            inv_data = self.parse_invoice(inv)
            details_array.append(inv_data)
        return data

    def parse_invoice(self, invoice):
        invoice.ensure_one()
        number = invoice.split_number()[1]

        date_invoice = datetime.strptime(invoice.date_invoice, '%Y-%m-%d')
        formatted_date_invoice = date_invoice.strftime('%Y%m%d')
        date_due = invoice.date_due and datetime.strptime(
            invoice.date_due, '%Y-%m-%d').strftime('%Y%m%d') or \
            formatted_date_invoice

        # Chequeamos si el concepto es producto,
        # servicios o productos y servicios
        product_service = [l.product_id and l.product_id.type or
                           'consu' for l in invoice.invoice_line]

        service = all([ps == 'service' for ps in product_service])
        products = all([ps == 'consu' or ps == 'product' for
                        ps in product_service])

        # Calculamos el concepto de la factura, dependiendo de las
        # lineas de productos que se estan vendiendo
        concept = None
        if products:
            concept = 1  # Productos
        elif service:
            concept = 2  # Servicios
        else:
            concept = 3  # Productos y Servicios

        doc_type = invoice.partner_id.document_type_id and \
            invoice.partner_id.document_type_id.afip_code or '99'
        doc_num = invoice.partner_id.vat or '0'

        currency_code = invoice.get_currency_code()
        # Cotizacion
        company_id = invoice.env.user.company_id
        company_currency_id = company_id.currency_id
        invoice_rate = 1.0
        if invoice.currency_id.id != company_currency_id.id:
            invoice_rate = invoice.currency_rate

        iva_values = self.get_iva_array(invoice)

        detail = {
            'invoice': invoice,
            'CbteDesde': number,
            'CbteHasta': number,
            'CbteFch': date_invoice.strftime('%Y%m%d'),
            'Concepto': concept,
            'DocNro': doc_num,
            'DocTipo': doc_type,
            'FchServDesde': False,
            'FchServHasta': False,
            'FchVtoPago': False,
            'MonId': currency_code,
            'MonCotiz': invoice_rate,
        }

        detail.update(iva_values)

        if concept in [2, 3]:
            detail.update({
                'FchServDesde': formatted_date_invoice,
                'FchServHasta': formatted_date_invoice,
                'FchVtoPago': date_due,
            })
        if not hasattr(self.data, 'sent_invoices'):
            self.data.sent_invoices = {}
        self.data.sent_invoices[invoice] = detail
        return detail

    def get_iva_array(self, invoice):
        invoice.ensure_one()
        conf = invoice.get_ws_conf()
        iva_array = []

        importe_neto = 0.0
        importe_operaciones_exentas = invoice.amount_exempt
        importe_iva = 0.0
        importe_tributos = 0.0
        importe_total = 0.0
        importe_neto_no_gravado = invoice.amount_no_taxed

        # Procesamos las taxes
        for tax in invoice.tax_line:
            found = False
            for eitax in conf.vat_tax_ids + conf.exempt_operations_tax_ids:
                if eitax.tax_code_id.id == tax.tax_code_id.id:
                    found = True
                    if eitax.exempt_operations:
                        pass
                        # importe_operaciones_exentas += tax.base
                    else:
                        importe_iva += tax.amount
                        importe_neto += tax.base
                        iva2 = {
                            'Id': int(eitax.code),
                            'BaseImp': tax.base,
                            'Importe': tax.amount
                        }
                        iva_array.append(iva2)
            if not found:
                importe_tributos += tax.amount

        importe_total = importe_neto + importe_neto_no_gravado + \
            importe_operaciones_exentas + importe_iva + importe_tributos

        invoice.check_invoice_total(importe_total)

        vals = {
            'number': invoice.internal_number,
            'id': invoice.id,
            'ImpIVA': importe_iva,
            'ImpNeto': importe_neto,
            'ImpOpEx': importe_operaciones_exentas,
            'ImpTotal': importe_total,
            'ImpTotConc': importe_neto_no_gravado,
            'ImpTrib': importe_tributos,
            'Iva': {
                'AlicIva': iva_array,
            },
        }
        log = ('Procesando Factura Electronica: %(number)s (id: %(id)s)\n' +
               'Importe Total: %(ImpTotal)s\n' +
               'Importe Neto Gravado: %(ImpNeto)s\n' +
               'Importe IVA: %(ImpIVA)s\n' +
               'Importe Operaciones Exentas: %(ImpOpEx)s\n' +
               'Importe Neto no Gravado: %(ImpTotConc)s\n' +
               'Array de IVA: %(Iva)s\n') % vals
        _logger.info(log)
        vals.pop('number')
        vals.pop('id')
        return vals

    def get_response_matching_invoice(self, resp):
        inv = False
        for inv, vals in self.data.sent_invoices.items():
            if resp['CbteDesde'] == vals['CbteDesde'] and \
                    resp['CbteFch'] == vals['CbteFch']:
                break
        return inv

    def parse_invoices_response(self, response):
        errores = []
        comprobantes = []

        if 'Errors' in response:
            for e in response.Errors.Err:
                error = '%s (Err. %s)' % (e.Msg, e.Code)
                errores.append(error)

        for det_response in response.FeDetResp.FECAEDetResponse:
            observaciones = []

            if 'Observaciones' in det_response:
                for o in det_response.Observaciones.Obs:
                    observacion = '%s (Err. %s)' % (o.Msg, o.Code)
                    observaciones.append(observacion)

            for det_req in \
                    self.last_request['args'][1].FeDetReq.FECAEDetRequest:
                if det_req['CbteDesde'] == det_response['CbteHasta'] and \
                        det_req['DocNro'] == det_req['DocNro']:
                    MonId = det_req['MonId']
                    MonCotiz = det_req['MonCotiz']
                    ImpTotal = det_req['ImpTotal']
                    break

            comp = {
                'Concepto': det_response.Concepto,
                'DocTipo': det_response.DocTipo,
                'DocNro': det_response.DocNro,
                'CbteDesde': det_response.CbteDesde,
                'CbteHasta': det_response.CbteHasta,
                'CbteFch': det_response.CbteFch,
                'Resultado': det_response.Resultado,
                'CAE': det_response.CAE,
                'CAEFchVto': det_response.CAEFchVto,
                'Observaciones': observaciones,
                'MonId': MonId,
                'MonCotiz': MonCotiz,
                'ImpTotal': ImpTotal,
            }
            invoice = self.get_response_matching_invoice(comp)
            comp['invoice'] = invoice
            comprobantes.append(comp)

        pos = invoice.split_number()[0]
        res = {
            'Comprobantes': comprobantes,
            'Errores': errores,
            'PtoVta': pos,
            'Resultado': response.FeCabResp.Resultado,
            'Reproceso': response.FeCabResp.Reproceso,
            'CbteTipo': response.FeCabResp.CbteTipo,
            'CantReg': response.FeCabResp.CantReg,
        }
        self.last_request['parse_result'] = res
        invoices_approved = {}

        # Verificamos el resultado de la Operacion
        # Si no fue aprobado
        if res['Resultado'] == 'R':
            msg = ''
            if res['Errores']:
                msg = 'Errores: ' + '\n'.join(res['Errores']) + '\n'
                msg = msg.encode('latin1').decode('utf8')

            if invoice._context.get('raise-exception', True):
                raise except_orm(_('AFIP Web Service Error'),
                                 _('La factura no fue aprobada. \n' +
                                   '%s') % msg)

        elif res['Resultado'] == 'A' or res['Resultado'] == 'P':
            for comp in res['Comprobantes']:
                invoice_vals = {}
                inv = comp['invoice']
                if comp['Observaciones']:
                    msg = 'Observaciones: ' + '\n'.join(comp['Observaciones'])

                # Chequeamos que se corresponda con la
                # factura que enviamos a validar
                doc_type = inv.partner_id.document_type_id and \
                    inv.partner_id.document_type_id.afip_code or '99'
                doc_tipo = comp['DocTipo'] == int(doc_type)
                doc_num = comp['DocNro'] == int(inv.partner_id.vat)
                cbte = True
                if inv.internal_number:
                    cbte = comp['CbteHasta'] == int(
                        inv.internal_number.split('-')[1])
                else:
                    # TODO: El nro de factura deberia unificarse
                    # para que se setee en una funcion
                    # o algo asi para que no haya posibilidad de que
                    # sea diferente nunca en su formato
                    invoice_vals['internal_number'] = '%04d-%08d' % (
                        res['PtoVta'], comp['CbteHasta'])

                if not all([doc_tipo, doc_num, cbte]):
                    raise except_orm(
                        _("WSFE Error!"),
                        _("Validated invoice that not corresponds!"))

                if comp['Resultado'] == 'A':
                    invoice_vals['cae'] = comp['CAE']
                    invoice_vals['cae_due_date'] = comp['CAEFchVto']
                    invoices_approved[inv.id] = invoice_vals

        return invoices_approved

    def log_request(self, environment):
        env = environment
        res = self.last_request['parse_result']
        wsfe_req_obj = env['wsfe.request']
        voucher_type_obj = env['wsfe.voucher_type']
        voucher_type = voucher_type_obj.search(
            [('code', '=', res['CbteTipo'])])
        voucher_type_name = voucher_type.name
        req_details = []
        pos = res['PtoVta']
        for index, comp in enumerate(res['Comprobantes']):

            # Esto es para fixear un bug que al hacer un refund,
            # si fallaba algo con la AFIP
            # se hace el rollback por lo tanto el refund que se estaba
            # creando ya no existe en
            # base de datos y estariamos violando una foreign
            # key contraint. Por eso,
            # chequeamos que existe info de la invoice_id,
            # sino lo seteamos en False
            read_inv = comp['invoice']

            if not read_inv:
                invoice_id = False
            else:
                invoice_id = read_inv.id

            det = {
                'name': invoice_id,
                'concept': str(comp['Concepto']),
                'doctype': comp['DocTipo'],  # TODO: Poner aca el nombre del tipo de documento  # noqa
                'docnum': str(comp['DocNro']),
                'voucher_number': comp['CbteHasta'],
                'voucher_date': comp['CbteFch'],
                'amount_total': comp['ImpTotal'],
                'cae': comp['CAE'],
                'cae_duedate': comp['CAEFchVto'],
                'result': comp['Resultado'],
                'currency': comp['MonId'],
                'currency_rate': comp['MonCotiz'],
                'observations': '\n'.join(comp['Observaciones']),
            }

            req_details.append((0, 0, det))

        # Chequeamos el reproceso
        reprocess = False
        if res['Reproceso'] == 'S':
            reprocess = True

        errors = '\n'.join(res['Errores']).encode('latin1').decode('utf8')
        vals = {
            'voucher_type': voucher_type_name,
            'nregs': len(res['Comprobantes']),
            'pos_ar': '%04d' % pos,
            'date_request': time.strftime('%Y-%m-%d %H:%M:%S'),
            'result': res['Resultado'],
            'reprocess': reprocess,
            'errors': errors,
            'detail_ids': req_details,
        }

        return wsfe_req_obj.create(vals)

    def send_invoice(self, invoice):
        data = self.parse_invoices(invoice)
        from pprint import pprint as pp
        pp(data)
        # TODO Remove No Check (And do proper checks)
        self.add(data, no_check='all')
        pp(self.data)
        response = self.request('FECAESolicitar')
        pp(response)
        approved = self.parse_invoices_response(response)
        return approved

###############################################################################

    def _get_errors(self, result):
        errors = []
        if 'Errors' in result:
            for error in result.Errors.Err:
                error = Error(error.Code, error.Msg)
                errors.append(error)
        return errors

    def _get_events(self, result):
        events = []
        if 'Events' in result:
            for event in result.Events.Evt:
                event = Event(event.Code, event.Msg)
                events.append(event)
        return events

    def check_errors(self, res, raise_exception=True):
        msg = ''
        if 'errors' in res:
            errors = [error.msg for error in res['errors']]
            err_codes = [str(error.code) for error in res['errors']]
            msg = ' '.join(errors)
            msg = msg + ' Codigo/s Error:' + ' '.join(err_codes)

            if msg != '' and raise_exception:
                raise except_orm(_('WSFE Error!'), msg)
        return msg

    def check_observations(self, res):
        msg = ''
        if 'observations' in res:
            observations = [obs.msg for obs in res['observations']]
            obs_codes = [str(obs.code) for obs in res['observations']]
            msg = ' '.join(observations)
            msg = msg + ' Codigo/s Observacion:' + ' '.join(obs_codes)

            # Escribimos en el log del cliente web
            _logger.info(msg)
        return msg

    def parse_response(self, result):
        res = {}
        # Obtenemos Errores y Eventos
        errors = self._get_errors(result)
        if len(errors):
            res['errors'] = errors

        events = self._get_events(result)
        if len(events):
            res['events'] = events

        res['response'] = result
        self.check_errors(res)
        self.check_observations(res)
        return res

###############################################################################

    def get_last_voucher(self, pos, voucher_type):
        token, sign = conf.wsaa_ticket_id.get_token_sign()

        _wsfe = wsfe(conf.cuit, token, sign, conf.url)
        res = _wsfe.fe_comp_ultimo_autorizado(pos, voucher_type)

        self.check_errors(res)
        self.check_observations(res)
        last = res['response']
        return last

###############################################################################
# Validation Methods
    NATURALS = ['CantReg', 'CbteTipo', 'PtoVta']

    @wsapi.check(NATURALS)
    def natural_number(val):
        val = int(val)
        if val > 0:
            return True
        return False
