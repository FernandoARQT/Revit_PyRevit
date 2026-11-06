"""Read-only H1 integration over real Rooms/ceilings, isolated proposal stores."""
import tempfile
from pathlib import Path
from ARQT_MEP import ownership
from ARQT_MEP.revit_read import fingerprint
from ARQT_MEP.routing import gui as editor_gui
from ARQT_MEP.routing.mcp import dispatch
from ARQT_MEP.routing.design import store


def run(doc,uidoc,artifacts,include_graph=False):
    identity=ownership.document_id(doc)
    before=bool(doc.IsModified);before_fingerprint=fingerprint(doc);report={};errors=[]
    old_design_root=store.ROOT
    old_messages=editor_gui.MessageBox
    class Messages:
        @staticmethod
        def Show(*args):errors.append(str(args[0]))
    try:
        with tempfile.TemporaryDirectory(prefix='arqt-v3-h1-') as folder:
            store.ROOT=str(Path(folder)/'routing')
            editor_gui.MessageBox=Messages
            def loaded(window,ui,state):
                try:
                    editor=state['design_editor'];editor.ui['Tab'].IsSelected=True
                    editor.create()
                    assert editor.proposal is not None,errors
                    source=editor.proposal.background
                    room=next(r for r in source['analysis']['rooms'] if r['level_id']==editor.get_level())
                    x,y=room['centroid']
                    editor.request('add_corridor',{'corridor_id':'native-h1','points_mm':[[x,y],[x+400,y],[x+800,y+300]],
                        'level_id':room['level_id'],'network_type':'SHARED'})
                    pid=editor.proposal.proposal_id
                    updated=dispatch(doc,'routing_design_move_vertex',{'proposal_id':pid,'expected_revision':2,
                        'corridor_id':'native-h1','index':1,'point_mm':[x+410.125,y+200.375]})
                    editor.poll();assert editor.proposal.revision==updated['revision']==3
                    editor.request('undo');editor.request('redo')
                    if include_graph:
                        analyzed=dispatch(doc,'routing_design_analyze',{'proposal_id':pid,'expected_revision':5,
                            'duct_profile':{'profile_id':'H2-envelope-test','network_type':'SUPPLY',
                                'nominal_diameter_mm':125,'internal_diameter_mm':100,'external_diameter_mm':125,
                                'insulation_thickness_mm':0,'minimum_bend_radius_mm':150,
                                'manufacturer_reference':'ENSAYO DE ENVOLVENTE; PRODUCTO NO VERIFICADO','geometry_verified':False},
                            'clearance_mm':10})
                        # Capture only in the native callback. Never wait for a
                        # scientific calculation inside a guarded WPF event.
                        import json
                        (Path(artifacts)/'routing-v3-h2-capture.json').write_text(json.dumps(analyzed,ensure_ascii=False),encoding='utf8')
                        editor.adopt(store.get(identity,pid))
                        report['spatial_capture']={'region_count':len(analyzed['snapshot']['regions']),
                            'profile_verified':False,'graph_calculation':'PENDING_EXTERNAL_PROCESS'}
                    from ARQT_MEP.routing.design.preview import export
                    capture=export(editor.proposal,room['level_id'],str(Path(artifacts)/'routing-v3-h1-native-plan.png'))
                    assert not errors,errors
                    assert bool(doc.IsModified)==before
                    assert fingerprint(doc)==before_fingerprint
                    report.update(rooms=len(source['analysis']['rooms']),terminals=len(source['terminals']),
                        source_version=source['version'],revision=editor.proposal.revision,embedded_wpf=True,host='TrazadoAutomaticoMEP',
                        mcp_shared_edits=True,undo_redo=True,isolated_stores=True,fingerprint_unchanged=True,
                        model_modified=bool(doc.IsModified),model_modified_before=before,capture=capture)
                except Exception:
                    import traceback
                    errors.append(traceback.format_exc())
                finally:window.Close()
            editor_gui.show_legacy(doc,uidoc,loaded)
    finally:
        store.ROOT=old_design_root
        editor_gui.MessageBox=old_messages
    if errors:raise AssertionError(errors)
    return report
