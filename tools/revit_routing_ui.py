"""Native modal UI load and read-only analysis test. No preview or storage writes."""
from ARQT_MEP.routing.gui import show
from System.Windows import RoutedEventArgs
from System.Windows.Controls import Button


def run(doc,uidoc):
    report={}
    def loaded(window,ui,state):
        try:
            report['source_rows']=state['origin_table'].Rows.Count
            report['destination_rows']=state['destination_table'].Rows.Count
            ui['Analyze'].RaiseEvent(RoutedEventArgs(Button.ClickEvent))
            assert state.get('snapshot') is not None
            assert state['snapshot']['stats']['region_count']>0
            report['regions']=state['snapshot']['stats']['region_count']
            report['initial_proposal_is_empty']=state['proposal'] is None
            report['title']=window.Title
        finally:window.Close()
    show(doc,uidoc,_on_loaded=loaded)
    return report
