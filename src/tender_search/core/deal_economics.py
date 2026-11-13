"""Scenario estimate, not a tax declaration. All taxes/costs are explicit inputs."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext

from .lot_pricing import calculate_price

CENT = Decimal('.01')
HUNDRED = Decimal(100)


def decimal_value(value, *, minimum=Decimal(0), maximum=Decimal('1000000000000000')):
    try:
        result = Decimal(str(value).replace(' ', '').replace('\u00a0', '').replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('Укажите корректное число.')
    if not result.is_finite() or not minimum <= result <= maximum or result.as_tuple().exponent < -6:
        raise ValueError('Число выходит за допустимые пределы.')
    return result


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_deal(*, unit_gross, quantity, sale_vat_rate, purchase_unit_gross,
                   purchase_total_gross=None,
                   purchase_vat_rate=0, purchase_vat_credit=False,
                   delivery_gross, delivery_vat_rate=0, delivery_vat_credit=False,
                   other_costs=0, refundable_security=0, freeze_days=0,
                   annual_money_rate=0, tax_mode='unknown', income_tax_rate=None):
    with localcontext() as ctx:
        ctx.prec = 60
        quantity = decimal_value(quantity, minimum=Decimal('.000001'), maximum=Decimal('1000000000000'))
        unit_gross = decimal_value(unit_gross, minimum=CENT)
        purchase_unit_gross = decimal_value(purchase_unit_gross, minimum=CENT)
        sale_vat_rate = decimal_value(sale_vat_rate, maximum=HUNDRED)
        purchase_vat_rate = decimal_value(purchase_vat_rate, maximum=HUNDRED)
        delivery_vat_rate = decimal_value(delivery_vat_rate, maximum=HUNDRED)
        delivery_gross = decimal_value(delivery_gross)
        other_costs = decimal_value(other_costs)
        refundable_security = decimal_value(refundable_security)
        freeze_days = decimal_value(freeze_days, maximum=Decimal(3650))
        annual_money_rate = decimal_value(annual_money_rate, maximum=HUNDRED)
        if tax_mode not in ('unknown', 'profit', 'turnover'):
            raise ValueError('Неизвестный способ расчёта налога.')
        sale = calculate_price(unit_gross, sale_vat_rate, quantity)
        if sale['total_net'] <= 0:
            raise ValueError('Сумма продажи после округления должна быть положительной.')
        purchase_gross = money(decimal_value(purchase_total_gross, minimum=CENT)) if purchase_total_gross is not None else money(purchase_unit_gross * quantity)
        purchase_credit = money(purchase_gross - purchase_gross / (1 + purchase_vat_rate / HUNDRED)) if purchase_vat_credit and sale_vat_rate > 0 else Decimal(0)
        delivery_credit = money(delivery_gross - delivery_gross / (1 + delivery_vat_rate / HUNDRED)) if delivery_vat_credit and sale_vat_rate > 0 else Decimal(0)
        purchase_net = purchase_gross - purchase_credit
        delivery_net = delivery_gross - delivery_credit
        profit_before_tax = money(sale['total_net'] - purchase_net - delivery_net - other_costs)
        tax = None
        if tax_mode != 'unknown':
            rate = decimal_value(income_tax_rate, maximum=HUNDRED)
            base = max(Decimal(0), profit_before_tax) if tax_mode == 'profit' else sale['total_net']
            tax = money(base * rate / HUNDRED)
        after_tax = money(profit_before_tax - tax) if tax is not None else None
        freeze_cost = money(refundable_security * annual_money_rate / HUNDRED * freeze_days / 365)
        # Opportunity cost is not automatically a deductible tax expense.
        economic_profit = money(after_tax - freeze_cost) if after_tax is not None else None
        return {
            'revenue_net': sale['total_net'], 'revenue_gross': sale['total_gross'],
            'output_vat': sale['total_vat'], 'purchase_gross': purchase_gross,
            'purchase_net': purchase_net, 'purchase_vat_credit': purchase_credit,
            'delivery_net': delivery_net, 'delivery_vat_credit': delivery_credit,
            'vat_balance': sale['total_vat'] - purchase_credit - delivery_credit,
            'other_costs': other_costs, 'profit_before_tax': profit_before_tax,
            'income_tax': tax, 'profit_after_tax': after_tax, 'freeze_cost': freeze_cost,
            'economic_profit': economic_profit, 'refundable_security': refundable_security,
            'operating_margin_pct': money(profit_before_tax / sale['total_net'] * HUNDRED),
            'margin_pct': money(economic_profit / sale['total_net'] * HUNDRED) if economic_profit is not None else None,
            # Includes refundable collateral as liquidity, never as a goods expense.
            'cash_required': money(purchase_gross + delivery_gross + other_costs + refundable_security),
            'tax_mode': tax_mode,
        }
