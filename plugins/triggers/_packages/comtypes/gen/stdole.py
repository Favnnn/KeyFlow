from enum import IntFlag

import comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 as __wrapper_module__
from comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0 import (
    Default, IEnumVARIANT, COMMETHOD, Font, _lcid, IFontEventsDisp,
    OLE_ENABLEDEFAULTBOOL, FONTNAME, FONTITALIC, FONTSTRIKETHROUGH,
    StdFont, DISPPROPERTY, OLE_YPOS_PIXELS, IPictureDisp,
    OLE_XPOS_CONTAINER, _check_version, IFontDisp, VARIANT_BOOL,
    OLE_YSIZE_CONTAINER, VgaColor, GUID, Checked, Color,
    FONTUNDERSCORE, StdPicture, OLE_OPTEXCLUSIVE, BSTR,
    OLE_YPOS_CONTAINER, Monochrome, FONTBOLD, Unchecked, IDispatch,
    IPicture, OLE_YPOS_HIMETRIC, CoClass, FONTSIZE, Library,
    OLE_COLOR, typelib_path, dispid, OLE_HANDLE, DISPPARAMS, IFont,
    OLE_CANCELBOOL, OLE_XPOS_PIXELS, Gray, OLE_XSIZE_CONTAINER,
    OLE_XPOS_HIMETRIC, Picture, OLE_XSIZE_HIMETRIC, EXCEPINFO,
    OLE_XSIZE_PIXELS, FontEvents, OLE_YSIZE_HIMETRIC, HRESULT,
    IUnknown, OLE_YSIZE_PIXELS, DISPMETHOD
)


class LoadPictureConstants(IntFlag):
    Default = 0
    Monochrome = 1
    VgaColor = 2
    Color = 4


class OLE_TRISTATE(IntFlag):
    Unchecked = 0
    Checked = 1
    Gray = 2


__all__ = [
    'Default', 'FONTUNDERSCORE', 'StdPicture', 'OLE_OPTEXCLUSIVE',
    'Font', 'OLE_YPOS_CONTAINER', 'Monochrome', 'FONTBOLD',
    'LoadPictureConstants', 'Unchecked', 'IPicture',
    'OLE_YPOS_HIMETRIC', 'IFontEventsDisp', 'FONTSIZE',
    'OLE_TRISTATE', 'Library', 'OLE_ENABLEDEFAULTBOOL', 'OLE_COLOR',
    'typelib_path', 'FONTNAME', 'OLE_HANDLE', 'IFont', 'FONTITALIC',
    'OLE_CANCELBOOL', 'FONTSTRIKETHROUGH', 'OLE_XPOS_PIXELS',
    'StdFont', 'Gray', 'OLE_YPOS_PIXELS', 'OLE_XSIZE_CONTAINER',
    'IPictureDisp', 'OLE_XPOS_HIMETRIC', 'OLE_XPOS_CONTAINER',
    'Picture', 'OLE_XSIZE_HIMETRIC', 'OLE_XSIZE_PIXELS', 'IFontDisp',
    'FontEvents', 'OLE_YSIZE_HIMETRIC', 'OLE_YSIZE_CONTAINER',
    'VgaColor', 'OLE_YSIZE_PIXELS', 'Checked', 'Color'
]

