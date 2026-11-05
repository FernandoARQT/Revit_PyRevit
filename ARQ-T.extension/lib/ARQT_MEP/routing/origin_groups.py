"""WPF equipment groups and equipment-wide selection; removable UI helper."""


def bind_origin_groups(table):
    from System import Array, Object, Boolean
    from System.Windows.Data import ListCollectionView, PropertyGroupDescription

    for name in ('GroupSelected', 'GroupEnabled'):
        if not table.Columns.Contains(name):
            table.Columns.Add(name, Boolean)
    groups = {}
    for row in table.Rows:
        groups.setdefault(str(row['Device']), []).append(row)
    updating = [False]

    def sync(rows):
        available = [r for r in rows if bool(r['Selectable'])]
        selected = bool(available) and all(bool(r['Include']) for r in available)
        for row in rows:
            if not row.IsNull('GroupSelected') and bool(row['GroupSelected']) == selected and not row.IsNull('GroupEnabled') and bool(row['GroupEnabled']) == bool(available):
                continue
            row['GroupSelected'] = selected
            row['GroupEnabled'] = bool(available)

    def changed(sender, args):
        if updating[0] or args.Column.ColumnName not in ('Include', 'GroupSelected'):
            return
        updating[0] = True
        try:
            rows = groups[str(args.Row['Device'])]
            if args.Column.ColumnName == 'GroupSelected':
                value = bool(args.Row['GroupSelected'])
                for row in rows:
                    row['Include'] = value and bool(row['Selectable'])
            elif not bool(args.Row['Selectable']):
                args.Row['Include'] = False
            sync(rows)
        finally:
            updating[0] = False

    for rows in groups.values():
        for row in rows:
            if not bool(row['Selectable']):
                row['Include'] = False
        sync(rows)
    table.ColumnChanged += changed
    view = ListCollectionView(Array[Object]([r for r in table.DefaultView]))
    view.GroupDescriptions.Add(PropertyGroupDescription('Device'))
    return view
