"""Identification must survive connector renumbering, rotation and ambiguous data."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ARQ-T.extension/lib'))
from ARQT_MEP.routing.connector_labels import connection_info,validate_origin


class ConnectorLabels(unittest.TestCase):
    device={'family':'I-VE-Recuperador de calor'}

    def test_four_air_functions_use_classification_and_flow_direction(self):
        cases=[('SupplyAir','Out','SUPPLY','habitaciones'),('ReturnAir','In','EXTRACT','baños'),('ExhaustAir','In','EXTRACT','baños'),
               ('SupplyAir','In','','Admisión'),('ExhaustAir','Out','','Expulsión')]
        for classification,direction,network,text in cases:
            port={'classification':classification,'flow_direction':direction,
                  'connector_id':999,'xyz_mm':[1,2,3],'direction':[0,-1,0]}
            info=connection_info(self.device,port)
            self.assertEqual(info['network'],network);self.assertIn(text,info['label'])
            self.assertEqual(info['kind'],'HRV')

    def test_exterior_and_unknown_ports_cannot_be_interior_origins(self):
        for classification,direction in [('SupplyAir','In'),('ExhaustAir','Out'),('OtherAir','Bidirectional')]:
            with self.assertRaises(ValueError):
                validate_origin(self.device,{'classification':classification,'flow_direction':direction},'SUPPLY')

    def test_interior_network_cannot_be_swapped(self):
        port={'classification':'ReturnAir','flow_direction':'In'}
        validate_origin(self.device,port,'EXTRACT')
        with self.assertRaises(ValueError):validate_origin(self.device,port,'SUPPLY')

    def test_distributor_does_not_receive_hrv_labels(self):
        info=connection_info({'family':'Distribuidor','is_configured_distributor':True},
                             {'network_type':'SUPPLY','classification':'SupplyAir','flow_direction':'Out'})
        self.assertFalse(info['is_hrv']);self.assertEqual(info['kind'],'DISTRIBUTOR')


if __name__=='__main__':unittest.main()
