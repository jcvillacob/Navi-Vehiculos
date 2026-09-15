from app.clients.quickserve_client import (
    _parse_dataplate,
    extract_cpl,
    extract_marketing_model_name,
    extract_service_model_name,
    extract_technical_engine_configuration,
)


def test_parse_dataplate_keeps_marketing_and_service_model_labels():
    html = """
    <table>
      <tr>
        <td>Marketing Model Name</td>
        <td>Service Model Name</td>
        <td>EPA Model Name</td>
      </tr>
      <tr>
        <td>L9 370</td>
        <td>L9 CM2450 L126B</td>
        <td>Not Available</td>
      </tr>
      <tr>
        <td>Shop Order</td>
        <td>Build Plant</td>
        <td>Build Date</td>
      </tr>
      <tr>
        <td>SOC3217</td>
        <td>CNS - ROCKY MOUNT (CDC)</td>
        <td>2024-04-10T00:00:00Z</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "L9 370"
    assert dataplate["Service Model Name"] == "L9 CM2450 L126B"
    assert "L9 370" not in dataplate
    assert extract_marketing_model_name(dataplate) == "L9 370"
    assert extract_service_model_name(dataplate) == "L9 CM2450 L126B"


def test_parse_dataplate_recovers_model_headers_when_value_row_was_isolated():
    html = """
    <table>
      <tr>
        <td>ISG13</td>
        <td>X13 CM2670 X122B</td>
        <td>Not Available</td>
      </tr>
      <tr>
        <td>Marketing Engine Configuration #</td>
        <td>Technical Engine Configuration #</td>
      </tr>
      <tr>
        <td>D1K3001BX03</td>
        <td>D1K3001BX03</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "ISG13"
    assert dataplate["Service Model Name"] == "X13 CM2670 X122B"
    assert dataplate["EPA Model Name"] == "Not Available"
    assert "ISG13" not in dataplate
    assert extract_marketing_model_name(dataplate) == "ISG13"
    assert extract_service_model_name(dataplate) == "X13 CM2670 X122B"


def test_parse_dataplate_maps_spanish_model_headers_without_extra_pair():
    html = """
    <table>
      <tr>
        <td>Nombre de modelo de marketing</td>
        <td>Nombre del modelo de servicio</td>
        <td>Nombre del modelo EPA</td>
      </tr>
      <tr>
        <td>ISG13</td>
        <td>X13 CM2670 X122B</td>
        <td>Not Available</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Marketing Model Name"] == "ISG13"
    assert dataplate["Service Model Name"] == "X13 CM2670 X122B"
    assert dataplate["EPA Model Name"] == "Not Available"
    assert "Nombre de modelo de marketing" not in dataplate
    assert "ISG13" not in dataplate


def test_parse_dataplate_keeps_cpl_in_multicolumn_spanish_header():
    html = """
    <table>
      <tr>
        <td>Technical Engine Configuration #</td>
        <td>N.º CPL</td>
        <td>Marketing Model Name</td>
      </tr>
      <tr>
        <td>D1K3001BX03</td>
        <td>4955</td>
        <td>L9 370</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Technical Engine Configuration #"] == "D1K3001BX03"
    assert extract_cpl(dataplate) == "4955"
    assert dataplate["Marketing Model Name"] == "L9 370"


def test_parse_dataplate_extracts_cpl_and_service_model_together_in_mixed_tables():
    html = """
    <table>
      <tr>
        <td>Marketing Model Name</td>
        <td>Service Model Name</td>
        <td>EPA Model Name</td>
      </tr>
      <tr>
        <td>ISG13</td>
        <td>X13 CM2670 X122B</td>
        <td>Not Available</td>
      </tr>
      <tr>
        <td>Technical Engine Configuration #</td>
        <td>D1K3001BX03</td>
      </tr>
      <tr>
        <td>N.º CPL</td>
        <td>5248</td>
      </tr>
      <tr>
        <td>Código de ECM</td>
        <td>KF10023</td>
      </tr>
      <tr>
        <td>Calibración de bomba de combustible</td>
        <td>HO06</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert extract_marketing_model_name(dataplate) == "ISG13"
    assert extract_service_model_name(dataplate) == "X13 CM2670 X122B"
    assert extract_technical_engine_configuration(dataplate) == "D1K3001BX03"
    assert extract_cpl(dataplate) == "5248"
    assert dataplate["Código de ECM"] == "KF10023"
    assert dataplate["Calibración de bomba de combustible"] == "HO06"


def test_parse_dataplate_extracts_cpl_in_four_column_key_value_tables():
    html = """
    <table>
      <tr>
        <td>Shop Order</td><td>SOC3217</td>
        <td>Build Plant</td><td>CNS - ROCKY MOUNT (CDC)</td>
      </tr>
      <tr>
        <td>Technical Engine Configuration #</td><td>D103042BX03</td>
        <td>N.º CPL</td><td>4955</td>
      </tr>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["Pedido a tienda"] == "SOC3217"
    assert dataplate["Planta de construcción"] == "CNS - ROCKY MOUNT (CDC)"
    assert extract_technical_engine_configuration(dataplate) == "D103042BX03"
    assert extract_cpl(dataplate) == "4955"


def test_parse_dataplate_extracts_cpl_from_thead_tbody_structure_and_links():
    html = """
    <table class="box" style="width:765px; margin-bottom:10px;">
      <thead>
        <tr>
          <th class="tbl_header" colspan="3">Product/Engine/System Dataplate - (ORIGINAL) VIN: 3HSPCTZT2PN083637</th>
        </tr>
        <tr>
          <th>Nombre de modelo de marketing</th>
          <th>Nombre del modelo de servicio</th>
          <th>EPA Nombre de modelo</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="box">ISG12</td>
          <td class="box">ISG12 CM2880 G107</td>
          <td class="box">No disponible</td>
        </tr>
      </tbody>
      <thead>
        <tr>
          <th>Pedido a tienda</th>
          <th>Planta de construcción</th>
          <th>Fecha de construcción</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="box">SO18535</td>
          <td class="box">BFC - BEIJING FOTON CUMMINS ENG C0</td>
          <td class="box">2022-08-11T00:00:00Z</td>
        </tr>
      </tbody>
      <thead>
        <tr>
          <th>Calibración de bomba de combustible</th>
          <th>Marketing Engine Configuration #</th>
          <th>Technical Engine Configuration #</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="box">HL53</td>
          <td class="box">D0S3001BX03</td>
          <td class="box">D0S3001BX03</td>
        </tr>
      </tbody>
      <thead>
        <tr>
          <th>N.º CPL</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="box"><a href="/qs3/portal/parts/cpl/index.html?cpl_num1=5376&amp;header=false" style="color:blue;" target="_blank">5376</a></td>
        </tr>
      </tbody>
    </table>
    """

    dataplate = _parse_dataplate(html)

    assert dataplate["VIN"] == "3HSPCTZT2PN083637"
    assert extract_marketing_model_name(dataplate) == "ISG12"
    assert extract_service_model_name(dataplate) == "ISG12 CM2880 G107"
    assert extract_technical_engine_configuration(dataplate) == "D0S3001BX03"
    assert extract_cpl(dataplate) == "5376"

