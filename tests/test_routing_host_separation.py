"""The terminal distributor stays at its pre-V3 baseline; V3 belongs to routing."""
import hashlib
import unittest
from pathlib import Path
from test_ventilation_editor import fixture
from ARQT_MEP.routing.design.background import from_catalog,check
from ARQT_MEP.routing.design.model import create

ROOT=Path(__file__).resolve().parents[1]


class HostSeparation(unittest.TestCase):
    def test_terminal_tool_matches_original_bytes(self):
        files={
            'ARQ-T.extension/lib/ARQT_MEP/editor_gui.py':'c5fc917e124b6f3930682ac9172a3ec64940acb28d752dc8ff8717130c51c255',
            'ARQ-T.extension/lib/ARQT_MEP/preview_image.py':'14be8c4670284f17f0691ee45f7ebcb00739ee0a1b57011baf9b4b9c02cdb7cf',
            'ARQ-T.extension/lib/ARQT_MEP/integration.py':'54261c08962c376392556a899f22b5aadc4e23337a8f8268edde05753eddff60',
            'ARQ-T.extension/ARQ-T.tab/MEP.panel/DistribuirVentilacion.pushbutton/script.py':'af9943be6be31c91efb10f6d8270b7d1ec033bfa87119e85c90c6d7974165a7d',
            'ARQ-T.extension/ARQ-T.tab/MEP.panel/DistribuirVentilacion.pushbutton/window.xaml':'9898fc24fba75edc8e1570cfcf6bd29bcb4093c3f14e8c54f474cafe1a5776ca'}
        for name,sha in files.items():self.assertEqual(hashlib.sha256((ROOT/name).read_bytes()).hexdigest(),sha,name)

    def test_v3_host_is_only_routing(self):
        self.assertIn('Name="DesignTab"',(ROOT/'ARQ-T.extension/ARQ-T.tab/MEP.panel/TrazadoAutomaticoMEP.pushbutton/window.xaml').read_text(encoding='utf8'))
        self.assertNotIn('design_editor',(ROOT/'ARQ-T.extension/lib/ARQT_MEP/editor_gui.py').read_text(encoding='utf8'))


class NativePlanBackground(unittest.TestCase):
    def data(self):
        source=fixture();catalog={'terminals':[{'id':'native-terminal','element_id':123,'room_uid':None,
            'airflow_lps':8,'connectors':[{'id':'port','xyz_mm':[3000,3000,2700]}]}]}
        selected=[{'terminal_id':'native-terminal','port_id':'port','network_type':'SUPPLY'}]
        return source,catalog,selected

    def test_native_context_does_not_require_a_distribution_proposal(self):
        source,catalog,selected=self.data()
        bg=from_catalog(source['analysis'],catalog,selected,'native-doc','geometry')
        self.assertEqual(bg['kind'],'RoutingPlanBackground');check(bg)
        p=create(bg);self.assertEqual(p.terminals['native-terminal']['level_id'],1)
        self.assertEqual(bg['terminals'][0]['room_uid'],'0');self.assertNotIn('flows',bg)

    def test_duplicate_terminal_or_missing_port_rejected(self):
        source,catalog,selected=self.data()
        with self.assertRaises(ValueError):from_catalog(source['analysis'],catalog,selected*2,'doc','geo')
        selected[0]['port_id']='missing'
        with self.assertRaises(ValueError):from_catalog(source['analysis'],catalog,selected,'doc','geo')

    def test_empty_selection_still_allows_manual_corridors(self):
        source,catalog,selected=self.data()
        p=create(from_catalog(source['analysis'],catalog,[],'doc','geo'))
        self.assertFalse(p.terminals);self.assertTrue(p.background['analysis']['rooms'])


if __name__=='__main__':unittest.main()
