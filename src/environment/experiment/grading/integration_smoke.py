"""Real ORM smoke checks in a rollback-only transaction on a disposable image."""
import argparse
from decimal import Decimal as D
import json
import django
django.setup()
from django.db import transaction
from oscar.core.loading import get_model

parser=argparse.ArgumentParser()
parser.add_argument('--expect',choices=['baseline','buggy'],required=True)
args=parser.parse_args()
Order=get_model('order','Order')
Source=get_model('payment','Source')
SourceType=get_model('payment','SourceType')
StockRecord=get_model('partner','StockRecord')
with transaction.atomic():
    order=Order.objects.get(number='100099')
    source_type=SourceType.objects.create(name='Disposable verification')
    source=Source.objects.create(order=order,source_type=source_type,currency='GBP',amount_allocated=D(100))
    source.debit(D(10))
    debit_transaction=source.transactions.get(txn_type='Debit')
    source.refund(D(2))
    source.refresh_from_db()
    stock=StockRecord.objects.filter(product_id=12).first()
    assert stock is not None and stock.can_track_allocations
    StockRecord.objects.filter(pk=stock.pk).update(num_in_stock=10,num_allocated=0)
    stock.refresh_from_db()
    stock.allocate(3)
    allocated=stock.num_allocated
    available=stock.net_stock_level
    StockRecord.objects.filter(pk=stock.pk).update(num_in_stock=10,num_allocated=4)
    stock.refresh_from_db()
    stock.consume_allocation(2)
    physical_after_shipping=stock.num_in_stock
    stock.cancel_allocation(1)
    actual=dict(debit=str(source.amount_debited),transaction_amount=str(debit_transaction.amount),refund=str(source.amount_refunded),
                allocated=allocated,available=available,physical_after_shipping=physical_after_shipping,allocated_after_cancel=stock.num_allocated)
    expected=dict(debit=D(20) if args.expect=='buggy' else D(10),transaction_amount=D(0) if args.expect=='buggy' else D(10),
                  refund=D(-2) if args.expect=='buggy' else D(2),allocated=1 if args.expect=='buggy' else 3,
                  available=11 if args.expect=='buggy' else 7,physical_after_shipping=10 if args.expect=='buggy' else 8,
                  allocated_after_cancel=3 if args.expect=='buggy' else 1)
    for key,value in expected.items():
        assert D(str(actual[key]))==D(str(value)),(key,actual[key],value)
    transaction.set_rollback(True)
print(json.dumps(dict(status='passed',image_kind=args.expect,actual=actual,transaction_rolled_back=True)))
