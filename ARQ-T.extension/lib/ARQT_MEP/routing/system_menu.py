"""Extensible system entry menu. Only heat recovery is currently implemented."""
def select_system(_on_loaded=None):
    import clr
    clr.AddReference('PresentationFramework')
    from System.Windows.Markup import XamlReader
    window=XamlReader.Parse('''<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation" Title="ARQ-T · Sistemas MEP" Width="460" Height="260" ResizeMode="NoResize" WindowStartupLocation="CenterScreen" Background="#F5F6F8" FontFamily="Century Gothic"><StackPanel Margin="24"><TextBlock Text="¿Qué sistema quieres diseñar?" FontSize="20" Margin="0,0,0,20"/><Button Name="HeatRecovery" Content="Recuperación de calor" Padding="16" Background="#F47721"/><TextBlock Text="Diseño de impulsión y extracción con recuperador.\nSe incorporarán más sistemas a este menú." TextWrapping="Wrap" Margin="0,14,0,0"/></StackPanel></Window>''')
    selected=[]
    def choose(sender,args):selected.append('ventilation_radial');window.Close()
    window.FindName('HeatRecovery').Click+=choose
    if _on_loaded:window.Loaded+=lambda sender,args:_on_loaded(window)
    window.ShowDialog()
    return selected[0] if selected else None
