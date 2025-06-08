const { default: makeWASocket, DisconnectReason, useMultiFileAuthState } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const pino = require('pino');
const axios = require('axios');
const qrcode = require('qrcode-terminal');
require('dotenv').config(); // Charger les variables d'environnement

// Configuration de l'API FastAPI
const FASTAPI_BASE_URL = 'http://localhost:8000';

class WhatsAppBot {
    constructor() {
        this.sock = null;
        this.logger = pino({ level: 'silent' });

        // Configuration des contacts autorisés/exclus
        this.setupContactFilters();
    }

    setupContactFilters() {
        // Récupérer les listes depuis les variables d'environnement
        const includedOnlyEnv = process.env.INCLUDED_ONLY || '';
        const excludedEnv = process.env.EXCLUDED || '';

        // Convertir en tableaux et nettoyer les numéros
        this.includedOnly = this.parseContactList(includedOnlyEnv);
        this.excluded = this.parseContactList(excludedEnv);

        console.log('🔧 Configuration des filtres de contacts:');
        console.log(`📞 Contacts autorisés uniquement: ${this.includedOnly.length > 0 ? this.includedOnly.join(', ') : 'Tous autorisés'}`);
        console.log(`🚫 Contacts exclus: ${this.excluded.length > 0 ? this.excluded.join(', ') : 'Aucun exclu'}`);
    }

    parseContactList(contactString) {
        if (!contactString || contactString.trim() === '') {
            return [];
        }

        return contactString
            .split(',')
            .map(contact => contact.trim())
            .filter(contact => contact !== '')
            .map(contact => this.normalizePhoneNumber(contact));
    }

    normalizePhoneNumber(phoneNumber) {
        // Supprimer tous les caractères non numériques sauf le +
        let normalized = phoneNumber.replace(/[^\d+]/g, '');

        // Si le numéro commence par +, le garder
        if (normalized.startsWith('+')) {
            return normalized;
        }

        // Si le numéro commence par 00, remplacer par +
        if (normalized.startsWith('00')) {
            return '+' + normalized.substring(2);
        }

        return normalized;
    }

    extractPhoneFromJid(jid) {
        // Extraire le numéro de téléphone du JID WhatsApp
        // Format: numéro@s.whatsapp.net ou numéro@g.us (pour les groupes)
        const phoneNumber = jid.split('@')[0];
        return this.normalizePhoneNumber(phoneNumber);
    }

    isContactAllowed(jid, isGroup = false) {
        // Pour les groupes, on peut décider d'une logique différente
        if (isGroup) {
            // Option 1: Appliquer les mêmes règles aux groupes
            // Option 2: Autoriser tous les groupes (commenté ci-dessous)
            // return true;
        }

        const phoneNumber = this.extractPhoneFromJid(jid);

        // Vérifier si le contact est dans la liste d'exclusion
        if (this.excluded.length > 0) {
            const isExcluded = this.excluded.some(excludedNumber =>
                phoneNumber.includes(excludedNumber) || excludedNumber.includes(phoneNumber)
            );
            if (isExcluded) {
                console.log(`🚫 Contact exclu: ${phoneNumber}`);
                return false;
            }
        }

        // Si une liste "included_only" est définie, vérifier l'autorisation
        if (this.includedOnly.length > 0) {
            const isIncluded = this.includedOnly.some(includedNumber =>
                phoneNumber.includes(includedNumber) || includedNumber.includes(phoneNumber)
            );
            if (!isIncluded) {
                console.log(`📵 Contact non autorisé: ${phoneNumber}`);
                return false;
            }
        }

        console.log(`✅ Contact autorisé: ${phoneNumber}`);
        return true;
    }

    async startBot() {
        const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');

        this.sock = makeWASocket({
            auth: state,
            logger: this.logger,
            browser: ['WhatsApp Bot', 'Brave', '1.22.71'],
            defaultQueryTimeoutMs: 60000
        });

        this.sock.ev.on('creds.update', saveCreds);
        this.sock.ev.on('connection.update', this.handleConnection.bind(this));
        this.sock.ev.on('messages.upsert', this.handleMessages.bind(this));
    }

    handleConnection(update) {
        const { connection, lastDisconnect, qr } = update;

        // Gestion du QR Code
        if (qr) {
            console.log('\n🔗 QR Code généré - Scannez avec WhatsApp:');
            qrcode.generate(qr, { small: true });
            console.log('\n📱 Ouvrez WhatsApp > Paramètres > Appareils liés > Lier un appareil');
        }

        if (connection === 'close') {
            const shouldReconnect = (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;
            console.log('Connection fermée, reconnexion:', shouldReconnect);
            if (shouldReconnect) {
                this.startBot();
            }
        } else if (connection === 'open') {
            console.log('✅ Bot WhatsApp connecté avec succès!');
        } else if (connection === 'connecting') {
            console.log('🔄 Connexion en cours...');
        }
    }

    async handleMessages(m) {
        const msg = m.messages[0];
        if (!msg.message || msg.key.fromMe) return;

        const messageContent = this.extractMessageContent(msg);
        const from = msg.key.remoteJid;
        const senderName = msg.pushName || 'Utilisateur';
        const isGroup = from.endsWith('@g.us');
        const participantId = msg.key.participant || msg.participant;

        console.log(`📨 Message de ${senderName} (${from}): ${messageContent}`);
        console.log(`📍 Type: ${isGroup ? 'Groupe' : 'Privé'}`);

        // NOUVEAU: Vérifier si le contact est autorisé
        const contactToCheck = isGroup ? participantId : from;
        if (!this.isContactAllowed(contactToCheck, isGroup)) {
            console.log(`🔒 Message ignoré - Contact non autorisé`);
            return;
        }

        // Marquer le message comme lu
        await this.markAsRead(msg.key);

        // Vérifier si le bot est tagué dans un groupe
        const isBotMentioned = await this.isBotMentioned(msg, isGroup);

        // Ne répondre que si:
        // 1. Message privé OU
        // 2. Bot tagué dans le groupe OU
        // 3. Message commence par une commande (/)
        const shouldRespond = !isGroup || isBotMentioned || messageContent.startsWith('/');

        if (!shouldRespond) {
            console.log(`🔇 Message ignoré (groupe sans mention)`);
            return;
        }

        if (isBotMentioned) {
            console.log(`🏷️ Bot tagué dans le groupe !`);
        }

        // Simuler une latence humaine de 10ms avant de répondre
        await this.sleep(10);

        // Traitement du message via FastAPI
        try {
            // Afficher l'indicateur "en train d'écrire"
            await this.sendTyping(from);

            const response = await this.processMessageWithAI(
                messageContent,
                from,
                senderName,
                isGroup,
                isBotMentioned,
                participantId
            );

            // Dans un groupe, mentionner l'utilisateur dans la réponse
            if (isGroup && participantId) {
                await this.sendMessageWithMention(from, response, participantId, senderName, msg);
            } else {
                await this.sendMessage(from, response);
            }
        } catch (error) {
            console.error('❌ Erreur lors du traitement:', error);
            await this.sendMessage(from, "Désolé, je rencontre un problème technique. Réessayez plus tard.");
        }
    }

    extractMessageContent(msg) {
        if (msg.message.conversation) {
            return msg.message.conversation;
        } else if (msg.message.extendedTextMessage) {
            return msg.message.extendedTextMessage.text;
        }
        return '';
    }

    async isBotMentioned(msg, isGroup) {
        if (!isGroup) return false;

        try {
            // Récupérer l'ID du bot
            const botId = this.sock.user.id;
            const botNumber = botId.split(':')[0];

            // Vérifier les mentions dans le message
            const messageContent = this.extractMessageContent(msg);
            const mentions = msg.message?.extendedTextMessage?.contextInfo?.mentionedJid || [];

            // Vérifier si le bot est dans les mentions
            const isMentioned = mentions.some(mention =>
                mention.includes(botNumber) || mention.includes(botId)
            );

            // // Vérifier aussi par le texte (au cas où)
            // const isTextMention = messageContent.includes(`@${botNumber}`) ||
            //                     messageContent.includes('@bot') ||
            //                     messageContent.includes('@chatbot') ||
            //                     messageContent.includes("@47700225572929");

            return isMentioned || isTextMention;
        } catch (error) {
            console.error('❌ Erreur vérification mention:', error);
            return false;
        }
    }

    async processMessageWithAI(message, userId, userName, isGroup = false, isMentioned = false, participantId = null) {
        try {
            const response = await axios.post(`${FASTAPI_BASE_URL}/chat`, {
                message: message,
                user_id: userId,
                user_name: userName,
                is_group: isGroup,
                is_mentioned: isMentioned,
                participant_id: participantId
            }, {
                headers: {
                    'Content-Type': 'application/json'
                },
                timeout: 100000 // 100 secondes
            });

            // Simuler un délai de réflexion réaliste (entre 500ms et 2s)
            const thinkingTime = Math.random() * 1500 + 500;
            await this.sleep(thinkingTime);

            return response.data.response;
        } catch (error) {
            console.error('Erreur API FastAPI:', error.message);
            throw error;
        }
    }

    async sendMessage(to, message) {
        try {
            await this.sock.sendMessage(to, { text: message });
            console.log(`📤 Message envoyé à ${to}: ${message}`);
        } catch (error) {
            console.error('❌ Erreur envoi message:', error);
        }
    }

    async sendMessageWithMention(to, message, mentionJid, mentionName, quotedMessage) {
        try {
            // Ajoute le tag visuel @numéro dans le message
            const tag = `@${mentionJid.split('@')[0]}`;
            const fullMessage = `${tag} ${message}`;

            await this.sock.sendMessage(to, {
                text: fullMessage,
                mentions: [mentionJid]
            }, {
                quoted: quotedMessage
            });

            console.log(`📤 Message avec mention envoyé à ${to}: ${fullMessage}`);
        } catch (error) {
            console.error('❌ Erreur envoi message avec mention:', error);
            // Fallback sans mention (avec le nom affiché en clair)
            await this.sendMessage(to, `${mentionName}: ${message}`);
        }
    }

    async getGroupInfo(groupId) {
        try {
            const groupMetadata = await this.sock.groupMetadata(groupId);
            return {
                name: groupMetadata.subject,
                participants: groupMetadata.participants.length,
                admins: groupMetadata.participants.filter(p => p.admin).length
            };
        } catch (error) {
            console.error('❌ Erreur récupération info groupe:', error);
            return null;
        }
    }

    // Méthodes utilitaires
    async markAsRead(messageKey) {
        try {
            await this.sock.readMessages([messageKey]);
            console.log(`✅ Message marqué comme lu`);
        } catch (error) {
            console.error('❌ Erreur marquage message lu:', error);
        }
    }

    async sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }

    async sendTyping(to, duration = 2000) {
        try {
            await this.sock.sendPresenceUpdate('composing', to);
            // Arrêter l'indicateur après la durée spécifiée
            setTimeout(async () => {
                await this.sock.sendPresenceUpdate('paused', to);
            }, duration);
        } catch (error) {
            console.error('❌ Erreur indicateur frappe:', error);
        }
    }

    async sendImage(to, imagePath, caption = '') {
        try {
            await this.sock.sendMessage(to, {
                image: { url: imagePath },
                caption: caption
            });
        } catch (error) {
            console.error('❌ Erreur envoi image:', error);
        }
    }

    async sendDocument(to, documentPath, fileName) {
        try {
            await this.sock.sendMessage(to, {
                document: { url: documentPath },
                fileName: fileName,
                mimetype: 'application/pdf'
            });
        } catch (error) {
            console.error('❌ Erreur envoi document:', error);
        }
    }
}

// Démarrage du bot
const bot = new WhatsAppBot();
bot.startBot().catch(console.error);

// Gestion gracieuse de l'arrêt
process.on('SIGINT', () => {
    console.log('\n🛑 Arrêt du bot...');
    process.exit(0);
});

module.exports = WhatsAppBot;