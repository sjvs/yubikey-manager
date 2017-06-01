# Copyright (c) 2017 Yubico AB
# All rights reserved.
#
#   Redistribution and use in source and binary forms, with or
#   without modification, are permitted provided that the following
#   conditions are met:
#
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    2. Redistributions in binary form must reproduce the above
#       copyright notice, this list of conditions and the following
#       disclaimer in the documentation and/or other materials provided
#       with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

from __future__ import absolute_import

from ..util import TRANSPORT
from ..piv import (
    PivController, ALGO, OBJ, SW, SLOT, PIN_POLICY, TOUCH_POLICY,
    DEFAULT_MANAGEMENT_KEY)
from ..driver_ccid import APDUError, SW_APPLICATION_NOT_FOUND
from .util import click_skip_on_help, click_callback
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend
from cryptography.x509.oid import NameOID
from cryptography import utils
from binascii import b2a_hex, a2b_hex
import click
import os
import datetime


def one_of(data):
    def inner(ctx, param, key):
        if key is not None:
            return data[key]
    return inner


def get_or_fail(data):
    def inner(key):
        if key in data:
            return data[key]
        raise ValueError('Invalid value: {}. Must be one of: {}'.format(
            key, ', '.join(data.keys())))
    return inner


def int_in_range(minval, maxval):
    def inner(val):
        intval = int(val)
        if minval <= intval <= maxval:
            return intval
        raise ValueError('Invalid value: {}. Must be in range {}-{}'.format(
            intval, minval, maxval))
    return inner


@click_callback()
def click_parse_piv_slot(ctx, param, val):
    try:
        return SLOT(int(val, 16))
    except:
        raise ValueError(val)


@click_callback()
def click_parse_cert_format(ctx, param, val):
    if val == 'PEM':
        return serialization.Encoding.PEM
    elif val == 'DER':
        return serialization.Encoding.DER
    else:
        raise ValueError(val)


@click_callback()
def click_parse_management_key(ctx, param, val):
    try:
        return a2b_hex(val)
    except:
        return ValueError(val)


click_slot_argument = click.argument('slot', callback=click_parse_piv_slot)
click_input_argument = click.argument('input', type=click.File('rb'))
click_output_argument = click.argument('output', type=click.File('wb'))
click_management_key_option = click.option(
    '-m', '--management-key',
    help='The management key.',
    callback=click_parse_management_key)
click_pin_option = click.option(
    '-P', '--pin', help='PIN code.')
click_key_format_option = click.option(
    '-f', '--key-format', type=click.Choice(['PEM', 'DER']),
    default='PEM', help='Key serialization format.')
click_cert_format_option = click.option(
    '-f', '--cert-format',
    type=click.Choice(['PEM', 'DER']), default='PEM',
    help='Certificate format.', callback=click_parse_cert_format)
click_pin_policy_option = click.option(
    '-p', '--pin-policy',
    type=click.Choice(['DEFAULT', 'NEVER', 'ONCE', 'ALWAYS']),
    default='DEFAULT',
    help='PIN policy for slot.')
click_touch_policy_option = click.option(
    '-t', '--touch-policy',
    type=click.Choice(['DEFAULT', 'NEVER', 'ALWAYS', 'CACHED']),
    default='DEFAULT',
    help='Touch policy for slot.')


@click.group()
@click.pass_context
@click_skip_on_help
def piv(ctx):
    """
    Manage YubiKey PIV functions.
    """
    try:
        controller = PivController(ctx.obj['dev'].driver)
        ctx.obj['controller'] = controller
    except APDUError as e:
        if e.sw == SW_APPLICATION_NOT_FOUND:
            ctx.fail("The applet can't be found on the device.")
        raise


@piv.command()
@click.pass_context
def info(ctx):
    """
    Display status of PIV functionality.
    """
    controller = ctx.obj['controller']
    click.echo('PIV version: %d.%d.%d' % controller.version)

    # Largest possible number of PIN tries to get back is 15
    tries = controller.get_pin_tries()
    tries = '15 or more.' if tries == 15 else tries
    click.echo('PIN tries remaining: %s' % tries)

    try:
        chuid = b2a_hex(controller.get_data(OBJ.CHUID)).decode()
    except APDUError as e:
        if e.sw == SW.NOT_FOUND:
            chuid = 'No data available.'
    click.echo('CHUID:\t' + chuid)

    try:
        ccc = b2a_hex(controller.get_data(OBJ.CAPABILITY)).decode()
    except APDUError as e:
        if e.sw == SW.NOT_FOUND:
            ccc = 'No data available.'
    click.echo('CCC: \t' + ccc)

    for (slot, cert) in controller.list_certificates().items():
        click.echo('Slot %02x:' % slot)
        click.echo('\tAlgorithm:\t%s' % ALGO.from_public_key(cert.public_key())
                   .name)
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        cn = cn[0].value if len(cn) > 0 else 'None'
        click.echo('\tSubject CN:\t%s' % cn)
        cn = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        cn = cn[0].value if len(cn) > 0 else 'None'
        click.echo('\tIssuer CN:\t%s' % cn)
        click.echo('\tFingerprint:\t%s' % b2a_hex(
            cert.fingerprint(hashes.SHA256())).decode('ascii'))
        click.echo('\tNot before:\t%s' % cert.not_valid_before)
        click.echo('\tNot after:\t%s' % cert.not_valid_after)


@piv.command()
@click.pass_context
@click.confirmation_option(
    '-f', '--force', prompt='WARNING! This will delete '
    'all stored PIV data and restore factory settings. Proceed?')
def reset(ctx):
    """
    Reset all PIV data.

    This action will wipe all credentials and reset factory settings for
    the PIV functionality on the device.
    """

    click.echo('Resetting PIV data...')
    ctx.obj['controller'].reset()
    click.echo(
        'Success! All credentials have been cleared from the device.')
    click.echo('Your YubiKey now has the default PIN, PUK and Management Key:')
    click.echo('\tPIN:\t123456')
    click.echo('\tPUK:\t12345678')
    click.echo(
        '\tManagement Key:\t010203040506070801020304050607080102030405060708')


@piv.command('generate-key')
@click.pass_context
@click_slot_argument
@click_management_key_option
@click_pin_option
@click.option(
    '-a', '--algorithm', help='Algorithm to use in key generation.',
    type=click.Choice(
        ['RSA1024', 'RSA2048', 'ECCP256', 'ECCP384']), default='RSA2048')
@click_key_format_option
@click_pin_policy_option
@click_touch_policy_option
@click_output_argument
def generate_key(
    ctx, slot, output, management_key, pin, algorithm, key_format, pin_policy,
        touch_policy):
    """
    Generate a asymmetric key pair.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)

    public_key = controller.generate_key(
        slot,
        ALGO.from_string(algorithm),
        PIN_POLICY.from_string(pin_policy),
        TOUCH_POLICY.from_string(touch_policy))
    key_encoding = serialization.Encoding.PEM \
        if key_format == 'PEM' else serialization.Encoding.DER
    output.write(public_key.public_bytes(
            encoding=key_encoding,
            format=serialization.PublicFormat.SubjectPublicKeyInfo))


@piv.command('import-certificate')
@click.pass_context
@click_slot_argument
@click_management_key_option
@click_pin_option
@click_input_argument
@click_cert_format_option
def import_certificate(ctx, slot, management_key, pin, input, cert_format):
    """
    Import a X.509 certificate.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)

    data = input.read()
    if cert_format == serialization.Encoding.PEM:
        cert = x509.load_pem_x509_certificate(data, default_backend())
    elif cert_format == serialization.Encoding.DER:
        cert = x509.load_der_x509_certificate(data, default_backend())
    controller.import_certificate(slot, cert)


@piv.command('import-key')
@click.pass_context
@click_slot_argument
@click_pin_option
@click_management_key_option
@click_key_format_option
@click_pin_policy_option
@click_touch_policy_option
@click_input_argument
def import_key(
        ctx, slot, management_key, pin, input,
        key_format, pin_policy, touch_policy):
    """
    Import a private key.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)

    data = input.read()
    password = None  # TODO: add support
    if key_format == 'PEM':
        private_key = serialization.load_pem_private_key(
            data, password=password,
            backend=default_backend())
    elif key_format == 'DER':
        private_key = serialization.load_der_private_key(
            data, password=password,
            backend=default_backend())

    controller.import_key(
            slot,
            private_key,
            pin_policy=PIN_POLICY.from_string(pin_policy),
            touch_policy=TOUCH_POLICY.from_string(touch_policy))


@piv.command()
@click.pass_context
@click_slot_argument
@click_cert_format_option
@click_output_argument
def attest(ctx, slot, output, cert_format):
    """
    Generate a attestation certificate for a key.
    """
    controller = ctx.obj['controller']
    try:
        cert = controller.attest(slot)
    except APDUError:
        ctx.fail('Attestation failed.')
    output.write(cert.public_bytes(encoding=cert_format))


@piv.command('export-certificate')
@click.pass_context
@click_slot_argument
@click_cert_format_option
@click_output_argument
def export_certificate(ctx, slot, cert_format, output):
    """
    Export a X.509 certificate.
    """
    controller = ctx.obj['controller']
    try:
        cert = controller.read_certificate(slot)
    except APDUError as e:
        if e.sw == SW.NOT_FOUND:
            ctx.fail('No certificate found.')
    output.write(cert.public_bytes(encoding=cert_format))


@piv.command()
@click.pass_context
@click_pin_option
@click_management_key_option
def init(ctx, management_key, pin):
    """
    Generate a CHUID and CCC on the device.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)

    controller.update_chuid()
    controller.update_ccc()
    click.echo('A CHUID and CCC generated.')


@piv.command('set-pin-retries')
@click.pass_context
@click.argument(
    'pin-retries', type=click.IntRange(1, 255), metavar='PIN-RETRIES')
@click.argument(
    'puk-retries', type=click.IntRange(1, 255), metavar='PUK-RETRIES')
@click_management_key_option
@click_pin_option
def set_pin_retries(ctx, management_key, pin, pin_retries, puk_retries):
    """
    Set the number of PIN and PUK retries.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)

    controller.set_pin_retries(pin_retries, puk_retries)


@piv.command('generate-certificate')
@click.pass_context
@click_slot_argument
@click_management_key_option
@click_pin_option
@click_input_argument
@click.option(
    '-s', '--subject', help='A subject name for the certificate', required=True)
@click.option(
    '-i', '--issuer', help='A issuer name for the certificate', required=True)
@click.option(
    '-d', '--valid-days',
    help='Number of days until the certificate expires.',
    type=click.INT, default=365)
def generate_certificate(
        ctx, slot, management_key, pin, input, subject, issuer, valid_days):
    """
    Generate a self-signed X.509 certificate.

    A private key need to exist in the slot.
    """
    controller = ctx.obj['controller']

    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)
        if not pin:
            pin = _prompt_pin(ctx)
        _verify_pin(ctx, controller, pin)

    data = input.read()
    public_key = serialization.load_pem_public_key(
        data, default_backend())

    algorithm = ALGO.from_public_key(public_key)

    builder = x509.CertificateBuilder()
    builder = builder.public_key(public_key)
    builder = builder.subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject), ]))
    builder = builder.issuer_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer), ]))

    # x509.random_serial_number added in cryptography 1.6
    serial = utils.int_from_bytes(os.urandom(20), "big") >> 1
    builder = builder.serial_number(serial)

    now = datetime.datetime.now()
    builder = builder.not_valid_before(now)
    builder = builder.not_valid_after(now + datetime.timedelta(days=valid_days))

    try:
        cert = controller.sign_cert_builder(slot, algorithm, builder)
    except APDUError:
        ctx.fail('Certificate generation failed.')

    controller.import_certificate(slot, cert)


@piv.command('generate-csr')
@click.pass_context
@click_slot_argument
@click_input_argument
@click_output_argument
@click_pin_option
@click.option(
    '-s', '--subject',
    help='A subject name for the requested certificate.', required=True)
def generate_certificate_signing_request(
        ctx, slot, pin, input, output, subject):
    """
    Generate a Certificate Signing Request (CSR).

    A private key need to exist in the slot.
    """
    controller = ctx.obj['controller']
    if not pin:
        pin = _prompt_pin(ctx)
    _verify_pin(ctx, controller, pin)

    data = input.read()
    public_key = serialization.load_pem_public_key(
        data, default_backend())

    builder = x509.CertificateSigningRequestBuilder()
    builder = builder.subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject), ]))

    try:
        csr = controller.sign_csr_builder(slot, public_key, builder)
    except APDUError:
        ctx.fail('Certificate Signing Request generation failed.')
    output.write(csr.public_bytes(encoding=serialization.Encoding.PEM))


@piv.command('delete-certificate')
@click.pass_context
@click_slot_argument
@click_management_key_option
@click_pin_option
def delete_certificate(ctx, slot, management_key, pin):
    """
    Delete a certificate.
    """
    controller = ctx.obj['controller']
    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(ctx)
        _authenticate(ctx, controller, management_key)
    controller.delete_certificate(slot)


@piv.command('change-pin')
@click.pass_context
@click_pin_option
@click.option('-n', '--new-pin', help='A new PIN.')
def change_pin(ctx, pin, new_pin):
    """
    Change the PIN code.
    """
    controller = ctx.obj['controller']
    if not pin:
        pin = _prompt_pin(ctx, prompt='Enter your current PIN')
    if not new_pin:
        new_pin = click.prompt(
            'Enter your new PIN', default='', hide_input=True,
            show_default=False, confirmation_prompt=True)
    try:
        controller.change_pin(pin, new_pin)
    except APDUError:
        ctx.fail('Changing the PIN failed.')
    click.echo('New PIN set.')


@piv.command('change-puk')
@click.pass_context
@click.option('-p', '--puk', help='Current PUK code.')
@click.option('-n', '--new-puk', help='A new PUK code.')
def change_puk(ctx, puk, new_puk):
    """
    Change the PUK code.
    """
    controller = ctx.obj['controller']
    if not puk:
        puk = _prompt_pin(ctx, prompt='Enter your current PUK')
    if not new_puk:
        new_puk = click.prompt(
            'Enter your new PUK', default='', hide_input=True,
            show_default=False, confirmation_prompt=True)
    try:
        controller.change_puk(puk, new_puk)
    except APDUError:
        ctx.fail('Changing the PUK failed.')
    click.echo('New PUK set.')


@piv.command('change-management-key')
@click.pass_context
@click_management_key_option
@click_pin_option
@click.option(
    '-t', '--touch', is_flag=True,
    help='Require touch on YubiKey when prompted for management key.')
@click.option('-n', '--new-management-key', help='A new management key.')
def change_management_key(ctx, management_key, pin, new_management_key, touch):
    """
    Change the management key.
    """
    controller = ctx.obj['controller']

    if controller.has_derived_key:
        if not pin:
            pin = _prompt_pin(pin)
        controller.verify(pin)
    else:
        if not management_key:
            management_key = _prompt_management_key(
                ctx, prompt='Enter your current management key'
                            ' [blank to use the default key]')
        _authenticate(ctx, controller, management_key)
    if not new_management_key:
        new_management_key = click.prompt(
            'Enter your new management key',
            default='', show_default=False,
            hide_input=True, confirmation_prompt=True)
    try:
        new_management_key = a2b_hex(new_management_key)
    except:
        ctx.fail('New management key has the wrong format.')
    try:
        controller.set_mgm_key(new_management_key, touch=touch)
    except APDUError:
        ctx.fail('Changing the management key failed.')


def _prompt_management_key(
        ctx, prompt='Enter a management key [blank to use default key]'):
    management_key = click.prompt(
        prompt, default='', hide_input=True, show_default=False)
    if management_key == '':
        return DEFAULT_MANAGEMENT_KEY
    try:
        return a2b_hex(management_key)
    except:
        ctx.fail('Management key has the wrong format.')


def _prompt_pin(ctx, prompt='Enter PIN'):
    return click.prompt(
        prompt, default='', hide_input=True, show_default=False)


def _verify_pin(ctx, controller, pin):
    try:
        controller.verify(pin)
    except APDUError:
        ctx.fail('PIN verification failed.')


def _authenticate(ctx, controller, management_key):
    try:
        controller.authenticate(management_key)
    except APDUError:
        ctx.fail('Authentication with management key failed.')


piv.transports = TRANSPORT.CCID
