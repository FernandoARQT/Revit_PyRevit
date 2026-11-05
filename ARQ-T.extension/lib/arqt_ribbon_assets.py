import os
import io
import clr
clr.AddReference('PresentationCore')
from System.IO import FileStream,FileMode,FileAccess,FileShare
from System.Windows.Media.Imaging import BitmapImage,BitmapCacheOption,BitmapCreateOptions


def bitmap(path,small=False):
    stream=FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite)
    try:
        image=BitmapImage()
        image.BeginInit()
        image.CacheOption=BitmapCacheOption.OnLoad
        if small:image.DecodePixelWidth=16
        image.StreamSource=stream
        image.EndInit()
        image.Freeze()
        return image
    finally:
        stream.Close()


def apply(uiapp):
    root=os.path.dirname(os.path.dirname(__file__))
    updated=[]
    for panel in uiapp.GetRibbonPanels('ARQ-T'):
        if panel.Name not in ('ARQ','STR','MEP'):continue
        for button in panel.GetItems():
            if button.Name not in ('SuelosAutomaticos','Cavity','DespieceLosaAlveolar','DistribuirVentilacion','TrazadoAutomaticoMEP'):continue
            folder=os.path.join(root,'ARQ-T.tab',panel.Name+'.panel',button.Name+'.pushbutton')
            path=os.path.join(folder,'icon.png')
            try:
                from Autodesk.Revit.UI import UIThemeManager
                if 'dark' in str(UIThemeManager.CurrentTheme).lower():
                    path=os.path.join(folder,'icon.dark.png')
            except Exception:
                pass
            button.LargeImage=bitmap(path)
            button.Image=bitmap(path,True)
            with io.open(os.path.join(folder,'bundle.yaml'),'r',encoding='utf-8-sig') as source:
                raw=source.read()
            lines=raw.splitlines()
            start=next(i for i,line in enumerate(lines) if line.startswith('tooltip:'))+1
            text=[]
            for line in lines[start:]:
                if line and not line.startswith(' '):break
                text.append(line.strip())
            paragraphs='\n'.join(text).strip().split('\n\n',1)
            button.ToolTip=paragraphs[0]
            button.LongDescription=paragraphs[1] if len(paragraphs)>1 else ''
            updated.append({'button':button.Name,'pixels':[button.LargeImage.PixelWidth,button.LargeImage.PixelHeight],'summary':button.ToolTip})
    return updated
