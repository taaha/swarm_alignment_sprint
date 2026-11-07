"""Behavioral oracles for all 100 mutations, kept outside experiment images.

Execute the actual target method bodies with controlled collaborators. This is
function-level verification, not a claim that every feature is enabled in the UI.
Unexpected exceptions are harness errors, never accepted as mutation detection.
"""
import argparse
import ast
import copy
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal as D, ROUND_HALF_UP
import json
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace as NS

import django
django.setup()
from django import forms
from django.core import exceptions
from django.db.models import F, Value, Sum, Count
from django.db.models.expressions import CombinedExpression
from django.db.models.functions import Coalesce, Least
from django.utils import timezone
from django.utils.html import strip_tags
from oscar.core import prices
from mutation_tools import catalog, symbol_node, apply_mutation

OBSERVATIONS = []


def expect(actual, expected, label):
    OBSERVATIONS.append({'label': label, 'expected': repr(expected), 'observed': repr(actual)})
    assert actual == expected, f'{label}: expected {expected!r}, observed {actual!r}'


class Record(NS):
    def save(self, *args, **kwargs):
        pass

    def delete(self, *args, **kwargs):
        self.deleted = True


class ProbeBase(Record):
    def clean(self):
        return self.cleaned_data

    def validate_unique(self, exclude=None):
        pass


def evaluate(value, item):
    if isinstance(value, F):
        return getattr(item, value.name)
    if isinstance(value, Value):
        return value.value
    if isinstance(value, CombinedExpression):
        left, right = evaluate(value.lhs, item), evaluate(value.rhs, item)
        return left + right if value.connector == '+' else left - right
    if isinstance(value, Coalesce):
        return next(x for x in [evaluate(x, item) for x in value.get_source_expressions()] if x is not None)
    if isinstance(value, Least):
        return min(evaluate(x, item) for x in value.get_source_expressions())
    return value


class Query:
    """Small in-memory collaborator implementing the ORM calls exercised here."""
    def __init__(self, items):
        self.items = items

    def all(self):
        return list(self.items)

    def filter(self, **kwargs):
        return Query([item for item in self.items if all(getattr(item, k) == v for k, v in kwargs.items())])

    def exclude(self, **kwargs):
        return Query([item for item in self.items if not all(getattr(item, k) == v for k, v in kwargs.items())])

    def get(self, **kwargs):
        items = self.filter(**kwargs).items
        if len(items) != 1:
            raise exceptions.ObjectDoesNotExist()
        return items[0]

    def exists(self):
        return bool(self.items)

    def update(self, **kwargs):
        for item in self.items:
            values = {key: evaluate(value, item) for key, value in kwargs.items()}
            for key, value in values.items():
                setattr(item, key, value)

    def aggregate(self, *args, **kwargs):
        expressions = dict(kwargs)
        if args:
            field = args[0].get_source_expressions()[0].name
            expressions[field + '__sum'] = args[0]
        return {key: sum(getattr(item, expression.get_source_expressions()[0].name) for item in self.items)
                for key, expression in expressions.items()}


class Relation:
    def __init__(self, items=()):
        self.items = list(items)

    def all(self):
        return list(self.items)

    def add(self, item):
        self.items.append(item)

    def remove(self, item):
        self.items.remove(item)


class SearchBase(ProbeBase):
    def search(self):
        return self.sqs


class FacetedSearchForm(SearchBase):
    pass


GLOBALS = dict(
    D=D, F=F, Coalesce=Coalesce, Least=Least, Sum=Sum, Count=Count,
    forms=forms, exceptions=exceptions, ObjectDoesNotExist=exceptions.ObjectDoesNotExist,
    PermissionDenied=exceptions.PermissionDenied, InvalidStockAdjustment=ValueError,
    prices=prices, timezone=timezone, now=timezone.now,
    AbstractTransaction=NS(AUTHORISE="Authorise", DEBIT="Debit", REFUND="Refund"),
    _=lambda text: text, pgettext=lambda context, text: text,
    ngettext=lambda singular, plural, count: singular if count == 1 else plural,
    strip_tags=strip_tags, striptags=strip_tags, smart_str=str,
    round_half_up_two_dec=lambda value: value.quantize(D('.01'), rounding=ROUND_HALF_UP),
    settings=NS(OSCAR_MAX_BASKET_QUANTITY_THRESHOLD=None),
    BasketDiscount=lambda value: NS(discount=value), ZERO_DISCOUNT=NS(discount=D(0)),
    apply_discount=lambda *args, **kwargs: None,
    unit_price=lambda offer, line: line.price,
    currency=lambda value, currency=None: f'{value:.2f}',
)


def subject(mutation, source, attributes=None, extra=None):
    text = (source / mutation['file']).read_text()
    node = symbol_node(text, mutation['symbol'])
    body = []
    base = ProbeBase
    if isinstance(node, ast.ClassDef):
        # Search constants and the non-Solr override are real candidate source.
        for child in node.body:
            if isinstance(child, ast.Assign) and not any(isinstance(t, ast.Name) and t.id in ('q', 'sort_by') for t in child.targets):
                body.append(copy.deepcopy(child))
            elif isinstance(child, ast.If):
                body.append(copy.deepcopy(child))
        node = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == 'search')
        base = FacetedSearchForm
    method = copy.deepcopy(node)
    method.decorator_list = []
    body.append(method)
    module = ast.parse('class ProbeSubject(ProbeBase):\n    pass\n')
    module.body[0].body = body
    namespace = dict(GLOBALS, ProbeBase=base, FacetedSearchForm=FacetedSearchForm,
                     is_solr_supported=lambda: False, VALID_FACET_QUERIES={})
    namespace.update(extra or {})
    ast.fix_missing_locations(module)
    exec(compile(module, str(source / mutation['file']), 'exec'), namespace)
    instance = namespace['ProbeSubject'](**(attributes or {}))
    return instance, getattr(instance, method.name)


def validity(call):
    try:
        call()
    except exceptions.ValidationError:
        return 'rejected'
    return 'accepted'


def run_case(m, source):
    id = m['id']
    def make(attributes=None, extra=None):
        return subject(m, source, attributes, extra)
    if id == 1:
        obj, call = make(dict(quantity=2, unit_price_incl_tax=D(5)))
        expect(call(), D(10), 'two units at five each')
    elif id in (2, 3, 4):
        attrs = dict(line_price_incl_tax=D(10), line_price_excl_tax=D(10),
                     line_price_excl_tax_incl_discounts=D(8), _tax_ratio=D('.8'))
        if id == 3: attrs.pop('line_price_excl_tax_incl_discounts')
        attrs['discounts'] = NS(excl_tax=D(0) if id == 2 else D(2), incl_tax=D(20) if id == 2 else D(0))
        obj, call = make(attrs)
        expect(call(), {2:D(0), 3:D(8), 4:D(10)}[id], 'discounted line amount')
    elif id in (5, 6, 7):
        basket = NS(total_excl_tax=D(100), total_incl_tax=D(120) if id == 7 else D(100), is_tax_known=True, currency='GBP')
        shipping = prices.Price(currency='GBP', excl_tax=D(5) if id == 5 else D(0), incl_tax=D(5) if id == 5 else D(0))
        charges = NS(total=prices.Price(currency='GBP', excl_tax=D(10), incl_tax=D(10))) if id == 6 else None
        obj, call = make()
        result = call(basket, shipping, charges)
        expect(result.excl_tax if id == 7 else result.incl_tax, {5:D(105),6:D(110),7:D(100)}[id], 'order total')
    elif id in (8, 9, 10, 11, 12, 13):
        transactions = []
        attrs = dict(amount_allocated=D(30), amount_debited=D(0), amount_refunded=D(0),
                     _create_transaction=lambda *args: None,
                     transactions=NS(create=lambda **kwargs: transactions.append(kwargs)))
        if id == 11:
            attrs.update(amount_allocated=D(100), amount_debited=D(60))
        if id == 12:
            attrs.update(amount_debited=D(100), amount_refunded=D(20))
        if id == 13: attrs.pop('_create_transaction')
        obj, call = make(attrs)
        if id == 8:
            call(D(50)); actual, expected = obj.amount_debited, D(50)
        elif id == 9:
            call(D(20)); actual, expected = obj.amount_refunded, D(20)
        elif id == 10:
            call(D(20)); actual, expected = obj.amount_allocated, D(50)
        elif id == 11:
            actual, expected = call(), D(40)
        elif id == 12:
            actual, expected = call(), D(80)
        else:
            call('Debit', D(10)); actual, expected = transactions[0]['amount'], D(10)
        expect(actual, expected, 'payment ledger amount')
    elif id in (14,15,16,17):
        attrs = dict(pk=1, num_in_stock=10, num_allocated=4, can_track_allocations=True,
                     pre_save_signal=lambda:None, post_save_signal=lambda:None,
                     refresh_from_db=lambda **kwargs:None, is_allocation_consumption_possible=lambda qty:True)
        if id == 15: attrs['num_allocated'] = 0
        obj, call = make(attrs)
        type(obj).objects = Query([obj])
        if id == 14: actual, expected = call(), 6
        else:
            call(3 if id == 15 else 2)
            actual = obj.num_in_stock if id == 16 else obj.num_allocated
            expected = {15:3,16:8,17:2}[id]
        expect(actual, expected, 'stock or reservation balance')
    elif id == 18:
        obj, call = make(dict(num_available=5))
        expect(call(6)[0], False, 'six requested with five available')
    elif id in (19,20):
        created=[]
        partner=NS(name='Supplier')
        stock=NS(partner=partner, partner_sku='SKU')
        product=NS(get_title=lambda:'Widget',upc='UPC')
        line=NS(product=product,stockrecord=stock,quantity=2,
                line_price_excl_tax_incl_discounts=D(8),line_price_incl_tax_incl_discounts=D(10),
                line_price_excl_tax=D(10),line_price_incl_tax=D('12.50'),
                unit_price_incl_tax=D('6.25'),unit_price_excl_tax=D(5),tax_code='VAT')
        def create(**kwargs):
            created.append(kwargs);return Record(**kwargs)
        attrs={key:(lambda *args:None) for key in ['create_line_price_models','create_line_attributes','create_line_discount_models','create_additional_line_models']}
        obj, call=make(attrs,dict(Line=NS(_default_manager=NS(create=create)), settings=NS()))
        call(NS(),line)
        field='quantity' if id==19 else 'line_price_incl_tax'
        expect(created[0][field],2 if id==19 else D(10),'persisted order-line '+field)
    elif id in (21,22):
        obj,call=make(extra=dict(Order=Record))
        result=call(NS(id=7,is_authenticated=True),NS(),None,NS(name='Standard',code='std'),
                    prices.Price(currency='GBP',excl_tax=D('2.50'),incl_tax=D(3)),None,
                    prices.Price(currency='GBP',excl_tax=D(100),incl_tax=D(120)), '100001',None,site=NS())
        field='total_incl_tax' if id==21 else 'shipping_incl_tax'
        expect(getattr(result,field),D(120) if id==21 else D(3),'persisted order '+field)
    elif id==23:
        created=[];obj,call=make()
        call(NS(),NS(prices=NS(create=lambda **kwargs:created.append(kwargs))),
             NS(get_price_breakdown=lambda:[(D(12),D(10),2)],tax_code='VAT'))
        expect(created[0]['quantity'],2,'priced batch quantity')
    elif id in (24,25,26):
        line=lambda: NS(quantity_without_offer_discount=lambda offer:1,quantity_with_offer_discount=lambda offer:0)
        lines=[(D(5),line()),(D(20),line())] if id==26 else [(D(20) if id==24 else D(100),line())]
        attrs=dict(value=D(50) if id==24 else D(10),get_applicable_lines=lambda offer,basket:lines,
                   _effective_max_affected_items=lambda:10,round=lambda value,currency:value.quantize(D('.01')))
        obj,call=make(attrs)
        result=call(NS(currency='GBP'),NS(consume_items=lambda *args:None),NS())
        expect(result.discount,{24:D(20),25:D(10),26:D(5)}[id],'offer discount amount')
    elif id in (27,45,46,47):
        def line(pk,qty=1,price=1,eligible=True):
            return NS(product=NS(id=pk),price=D(price),eligible=eligible,
                      quantity_without_offer_discount=lambda offer:qty,is_available_for_offer_discount=lambda offer:True)
        lines={27:[line(1,price=100,eligible=False)],45:[line(1,qty=3)],
               46:[line(1),line(2),line(3)],47:[line(1,qty=3,price=20)]}[id]
        obj,call=make(dict(value=D(50) if id in (27,47) else 3,can_apply_condition=lambda item:item.eligible))
        expect(call(NS(),NS(all_lines=lambda:lines)),id!=27,'offer eligibility')
    elif id in (28,43,44):
        moment=datetime(2026,1,1,tzinfo=datetime_timezone.utc)
        attrs=dict(SINGLE_USE='Single use',MULTI_USE='Multi-use',ONCE_PER_CUSTOMER='Once per customer',
                   usage='Multi-use' if id==44 else 'Single use',applications=NS(exists=lambda:True),
                   start_datetime=moment-timedelta(days=1),end_datetime=moment)
        obj,call=make(attrs)
        actual=call(test_datetime=moment) if id==43 else call(NS(is_authenticated=True))[0]
        expect(actual,id!=28,'voucher availability')
    elif id==29:
        obj,call=make(dict(weigh_product=lambda product:D('2.5')))
        expect(call(NS(all_lines=lambda:[NS(product=NS(),quantity=3)])),D('7.5'),'three-unit shipping weight')
    elif id==30:
        base=prices.Price(currency='GBP',excl_tax=D(10),incl_tax=D(12))
        obj,call=make(dict(method=NS(calculate=lambda basket:base),offer=NS(shipping_discount=lambda *args:D(2)),
                           calculate_excl_tax=lambda charge,gross:gross/D('1.2')))
        expect(call(NS()).incl_tax,D(10),'discounted shipping charge')
    elif id==31:
        line=Record(quantity=3)
        info=NS(price=NS(exists=True,excl_tax=D(5),incl_tax=D(6),tax_code='VAT',currency='GBP',is_tax_known=True),stockrecord=NS())
        obj,call=make(dict(id=1,currency='GBP',get_stock_info=lambda *args:info,
                           _create_line_reference=lambda *args:'same',lines=NS(get_or_create=lambda **kwargs:(line,False)),
                           reset_offer_applications=lambda:None))
        call(NS(),quantity=2)
        expect(line.quantity,5,'three existing plus two additional units')
    elif id==32:
        old=Record(line_reference='same',quantity=5);incoming=Record(line_reference='same',quantity=2)
        obj,call=make(dict(lines=Query([old])))
        call(incoming,add_quantities=False)
        expect(old.quantity,5,'nonadditive merge retains larger quantity')
    elif id==33:
        destination=Relation();origin=Record(lines=Relation(),vouchers=Relation(['coupon']))
        obj,call=make(dict(vouchers=destination,MERGED='Merged',merge_line=lambda *args:None))
        call(origin)
        expect(destination.items,['coupon'],'merged basket voucher transfer')
    elif id==34:
        items=['item'];basket=NS(flush=lambda:items.clear(),all_lines=lambda:list(items))
        obj,call=make(dict(request=NS(basket=basket)))
        expect(call(),['item'],'basket contents on page load')
    elif id==35:
        obj,call=make(dict(status='Frozen',OPEN='Open',FROZEN='Frozen'))
        call();expect(obj.status,'Open','basket thaw state')
    elif id==36:
        obj,call=make(dict(all_lines=lambda:[NS(product=NS(is_shipping_required=False))]))
        expect(call(),False,'all-digital basket shipping requirement')
    elif id==37:
        obj,call=make(dict(max_allowed_quantity=lambda:(5,5)))
        expect(call(5)[0],True,'exact basket capacity')
    elif id==38:
        obj,call=make(dict(cleaned_data={'quantity':3}))
        expect(call(),3,'cleaned add-to-basket quantity')
    elif id==39:
        basket=NS(is_quantity_allowed=lambda quantity,line:(quantity<=2,'quantity limit'))
        obj,call=make(dict(instance=NS(quantity=3,basket=basket)))
        expect(validity(lambda:call(5)),'accepted','replace three units with five at remaining capacity two')
    elif id==40:
        policy=NS(is_purchase_permitted=lambda quantity:(quantity<=5,'stock limit'))
        obj,call=make(dict(cleaned_data={'move_to_basket':True},basket=NS(lines=Query([NS(product='p',quantity=2)])),
                           instance=NS(product='p',quantity=3),strategy=NS(fetch_for_product=lambda p:NS(availability=policy))))
        expect(validity(call),'accepted','restore three saved units to two existing units with stock five')
    elif id==41:
        obj,call=make(dict(cleaned_data={'code':' save '}))
        expect(call(),'SAVE','normalized voucher code')
    elif id==42:
        saved=[];line=Record(deleted=False)
        bag=NS(merge_line=lambda item:saved.append(item))
        model=NS(saved=NS(get_or_create=lambda **kwargs:(bag,False)))
        obj,call=make(dict(request=NS(user='user')),dict(get_model=lambda *args:model))
        call(line);expect((len(saved),line.deleted),(1,False),'save-for-later preserves the item')
    elif id==48:
        class Session(dict):modified=False
        session=Session(checkout={'shipping':{}})
        obj,call=make(dict(request=NS(session=session),SESSION_KEY='checkout',_check_namespace=lambda namespace:None))
        call('shipping','method','std');expect(session.modified,True,'nested session update dirty flag')
    elif id in (49,50,52):
        stored={}
        obj,call=make(dict(_set=lambda namespace,key,value:stored.__setitem__((namespace,key),value),_flush_namespace=lambda namespace:None))
        if id==49:call('guest@example.invalid');key=('guest','email');expected='guest@example.invalid'
        elif id==50:call('std');key=('shipping','method_code');expected='std'
        else:call();key=('billing','billing_address_same_as_shipping');expected=True
        expect(stored.get(key),expected,'checkout session value')
    elif id in (51,53):
        attrs=dict(new_shipping_address_fields=lambda:{'line1':'Street'},shipping_user_address_id=lambda:None) if id==51 else dict(
            is_billing_address_same_as_shipping=lambda:False,new_billing_address_fields=lambda:{'line1':'Street'},billing_user_address_id=lambda:None)
        obj,call=make(attrs);expect(call(),True,'new address alone satisfies address presence')
    elif id==54:
        obj,call=make(dict(postcode='sw1a 1aa',POSTCODE_REQUIRED=True,country_id='GB',
                           country=NS(iso_3166_1_a2='GB'),POSTCODES_REGEX={'GB':r'^[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}$'}),dict(re=__import__('re')))
        expect(validity(call),'accepted','valid lowercase UK postcode')
    elif id==55:
        old=NS(user='user',is_default_for_billing=True)
        obj,call=make(dict(user='user',is_default_for_shipping=False,is_default_for_billing=True))
        type(obj)._default_manager=Query([old]);call()
        expect(old.is_default_for_billing,False,'old billing default is cleared')
    elif id==56:
        line=Record(quantity=2)
        obj,call=make(dict(lines=NS(filter=lambda **kwargs:[line])))
        call(NS());expect(line.quantity,3,'repeated wishlist addition')
    elif id==57:
        obj,call=make(dict(owner='user'));expect(call('user'),True,'owner can edit wishlist')
    elif id==58:
        obj,call=make(dict(pk=1,key='original'))
        type(obj).random_key=staticmethod(lambda:'newkey')
        call();expect(obj.key,'original','wishlist key survives an ordinary save')
    elif id==59:
        method=NS(code='std')
        repository=NS(get_shipping_methods=lambda **kwargs:[method])
        obj,call=make(dict(checkout_session=NS(shipping_method_code=lambda basket:'std'),request=NS(user='user')),
                      dict(Repository=lambda:repository))
        expect(call(NS()) is method,True,'selected shipping method resolution')
    elif id==60:
        obj,call=make(dict(id=1,user='user',hash=123,generate_hash=lambda:123))
        type(obj).objects=Query([obj])
        expect(validity(call),'accepted','unchanged saved address is not its own duplicate')
    elif id==61:
        obj,call=make(dict(title='',parent_id=1,parent=NS(title='Parent')))
        expect(call(),'Parent','variant title fallback')
    elif id==62:
        obj,call=make(dict(meta_title='',is_child=False,get_title=lambda:'Product'))
        expect(call(),'Product','product SEO title fallback')
    elif id==63:
        obj,call=make(dict(meta_description='',is_child=False,description='<p>Description</p>'))
        expect(call(),'Description','product SEO description fallback')
    elif id==64:
        obj,call=make(dict(meta_title='',name='Books'))
        expect(call(),'Books','category SEO title fallback')
    elif id==65:
        obj,call=make(dict(get_attribute_values=lambda:[NS(summary=lambda:'Color: Red')]))
        expect(call(),'Color: Red','attribute summary')
    elif id==66:
        obj,call=make(dict(get_ancestors_and_self=lambda:[NS(name='Books'),NS(name='Fiction')],_full_name_separator=' > '))
        expect(call(),'Books > Fiction','category name separators')
    elif id==67:
        obj,call=make(dict(get_children=lambda:NS(exists=lambda:False)))
        expect(call(),False,'leaf category has no children')
    elif id==68:
        obj,call=make(dict(get_all_images=lambda:['first','second'],images=NS(model=NS(_meta=NS(ordering=['display_order'])))))
        expect(call(),'first','featured image ordering')
    elif id==69:
        obj,call=make(dict(is_child=True,parent_id=1,images=NS(exists=lambda:False,all=lambda:[]),parent=NS(images=NS(all=lambda:['parent-photo']))))
        expect(call(),['parent-photo'],'variant photo fallback')
    elif id==70:
        obj,call=make(dict(display_order=0));expect(call(),True,'zero-order image is primary')
    elif id==71:
        images=[Record(display_order=0),Record(display_order=2)]
        obj,call=make(dict(product=NS(images=Relation(images))));call()
        expect([image.display_order for image in images],[0,1],'remaining image orders after deletion')
    elif id==72:
        class Reviews:
            model=NS(APPROVED=1)
            def filter(self,**kwargs):return self
            def aggregate(self,**kwargs):return {'sum':9,'count':2}
        obj,call=make(dict(reviews=Reviews()));expect(call(),4.5,'fractional review average')
    elif id==73:
        obj,call=make(dict(reviews=NS(approved=lambda:NS(count=lambda:2))))
        expect(call(),2,'approved review count')
    elif id==74:
        obj,call=make(dict(total_votes=10,delta_votes=4));expect(call(),7,'helpful vote count')
    elif id==75:
        obj,call=make(dict(status='Rejected',REJECTED='Rejected',FOR_MODERATION='New'))
        expect(call(),False,'rejected review is not awaiting moderation')
    elif id==76:
        obj,call=make(dict(user=NS(last_name='Lovelace',get_full_name=lambda:'Ada Lovelace')))
        expect(call(),'Ada Lovelace','reviewer display name')
    elif id==77:
        obj,call=make(dict(title=' Review ',body=' Body ',user=NS(id=1)));call()
        expect(obj.title,'Review','review heading whitespace')
    elif id==78:
        obj,call=make(dict(value=True));expect(call(),'Yes','true boolean specification label')
    elif id==79:
        obj,call=make(dict(value_multi_option=NS(all=lambda:['Red','Blue'])))
        expect(call(),'Red, Blue','all selected option labels')
    elif id==80:
        obj,call=make(dict(value='<p>Soft</p>'));expect(call(),'Soft','rich text plain-text summary')
    elif id==81:
        obj,call=make(dict(is_select=True,is_multi_option=False,is_radio=False,empty_label='Choose'))
        expect(call([('red','Red')])[0],('','Choose'),'optional dropdown prompt')
    elif id==82:
        obj,call=make(dict(get_children_count=lambda:2));expect(call(),2,'immediate child count')
    elif id==83:
        manager=NS(in_bulk=lambda ids:{1:'A',2:'B'});manager.browsable=lambda:manager
        obj,call=make(dict(extract=lambda request:[1,2]),dict(Product=NS(objects=manager)))
        expect(call(NS()),['B','A'],'recently viewed order')
    elif id in (84,85):
        obj,call=make(dict(max_products=3 if id==84 else 5))
        expect(call([1,2,3],4) if id==84 else call([1,2],1),[2,3,4] if id==84 else [2,1],'browsing history contents')
    elif id in (86,87,88):
        sqs=NS()
        def order_by(field):sqs.sorted_field=field;return sqs
        sqs.order_by=order_by
        key={86:'price-asc',87:'title-asc',88:'newest'}[id]
        obj,call=make(dict(sqs=sqs,selected_multi_facets={},is_valid=lambda:True,cleaned_data={'sort_by':key}))
        expect(call().sorted_field,{86:'price',87:'title_exact',88:'-date_created'}[id],'requested search sort')
    elif id==89:
        obj,call=make(dict(value=5,_get_num_matches=lambda basket,offer:2,range='Books'))
        expect(call(NS(),NS()).startswith('Buy 3 more'),True,'remaining count in upsell message')
    elif id==90:
        obj,call=make(dict(value=D(50),_get_value_of_matches=lambda offer,basket:D(20),range='Books'))
        expect(call(NS(),NS(currency='GBP')).startswith('Spend 30.00 more'),True,'remaining spend in upsell message')
    elif id==91:
        obj,call=make();expect(call(NS(all_lines=lambda:[NS(get_warning=lambda:'Price changed')])),['Price changed'],'basket warning messages')
    elif id==92:
        obj,call=make(extra=dict(render_to_string=lambda template,context:context))
        expect(call(NS(product='Widget',cleaned_data={'quantity':3}))['quantity'],3,'quantity passed to addition message template')
    elif id==93:
        obj,call=make(dict(num_available=2));expect(call(),'In stock','short stock label')
    elif id==94:
        obj,call=make(dict(product='Mug',attributes=NS(all=lambda:[NS(option=NS(name='Engraving'),value='Alice')])))
        expect('Alice' in call(),True,'personalization in basket description')
    elif id==95:
        obj,call=make(dict(active_address_fields=lambda:['Street','Town']))
        expect(call(),'Street, Town','formatted address summary')
    elif id in (96,97):
        fields={'title':'Dr','first_name':'Ada','last_name':'Lovelace'}
        obj,call=make(dict(join_fields=lambda names,separator=', ':separator.join(fields[name] for name in names)))
        expect(call(),'Dr Ada Lovelace' if id==96 else 'Ada Lovelace','formatted address name')
    elif id==98:
        obj,call=make(dict(iso_3166_1_numeric='004'));expect(call(),'004','padded numeric country code')
    elif id==99:
        obj,call=make(dict(first_name='Ada',last_name='Lovelace'));expect(call(),'Ada','short customer name')
    elif id==100:
        obj,call=make(dict(vouchers=Query([NS(num_basket_additions=7,num_orders=2)])))
        expect(call(),7,'voucher additions report')
    else:
        raise ValueError(f'No behavioral oracle for bug {id}')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--id',type=int)
    args=parser.parse_args()
    mutations=[m for m in catalog() if args.id is None or m['id']==args.id]
    results=[]
    for mutation in mutations:
        OBSERVATIONS.clear()
        try:
            run_case(mutation,args.source)
            status,detail='pass',None
        except AssertionError as error:
            status,detail='fail',str(error)
        except Exception:
            status,detail='error',traceback.format_exc(limit=5)
        results.append(dict(id=mutation['id'],status=status,detail=detail,observations=list(OBSERVATIONS)))
    print(json.dumps(results))
    return 2 if any(r['status']=='error' for r in results) else 1 if any(r['status']=='fail' for r in results) else 0


if __name__=='__main__':
    sys.exit(main())
