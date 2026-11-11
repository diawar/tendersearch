from django.db import migrations


def reconcile(apps,schema_editor):
    Workflow=apps.get_model('core','UserLotWorkflow')
    Collection=apps.get_model('core','CollectionLot')
    Preparation=apps.get_model('core','ApplicationPreparation')
    Price=apps.get_model('core','UserLotPrice')
    Queue=apps.get_model('core','DraftQueueItem')
    for state in Workflow.objects.filter(stage='applied').iterator():
        if Preparation.objects.filter(owner_id=state.owner_id,lot_id=state.lot_id,portal_state__in=['submitted','completed']).exists():
            continue
        state.stage='preparing'
        state.save(update_fields=['stage'])
        Collection.objects.filter(profile__owner_id=state.owner_id,lot_id=state.lot_id,stage='applied').update(stage='preparing')
        Price.objects.filter(owner_id=state.owner_id,lot_id=state.lot_id).update(locked_at=None)
        Queue.objects.get_or_create(owner_id=state.owner_id,lot_id=state.lot_id)


class Migration(migrations.Migration):
    dependencies=[('core','0038_structured_company_address')]
    operations=[migrations.RunPython(reconcile,migrations.RunPython.noop)]
